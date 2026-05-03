import os, json, re, base64, io
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

CLAUDE_KEY = os.environ.get('CLAUDE_API_KEY', '')

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'CardioVet CR Generator v7 BMP', 'key_set': bool(CLAUDE_KEY)})

def download_image(sb_url, sb_key, file_path):
    try:
        import requests
        url = f"{sb_url}/storage/v1/object/cardiovet-files/{file_path}"
        r = requests.get(url, headers={'apikey': sb_key, 'Authorization': f'Bearer {sb_key}'}, timeout=30)
        return r.content if r.status_code == 200 else None
    except Exception as e:
        print(f"Download error {file_path}: {e}")
        return None

def image_to_jpeg_b64(img_bytes, filename=''):
    """Convertit n'importe quel format image en JPEG base64"""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes))
        # Convertir en RGB si nécessaire (BMP peut être en modes divers)
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        # Redimensionner si trop grand
        max_w = 1024
        if img.width > max_w:
            ratio = max_w / img.width
            img = img.resize((max_w, int(img.height * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=85)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        print(f"Image conversion error {filename}: {e}")
        return None

def comment_single_image(img_b64, image_num, patient_info, key):
    """Claude Vision analyse une image échographique individuelle"""
    try:
        import anthropic as ac
        client_ai = ac.Anthropic(api_key=key)
        msg = client_ai.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=250,
            messages=[{
                'role': 'user',
                'content': [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                    {"type": "text", "text": (
                        f"Patient: {patient_info.get('animalName','?')}, "
                        f"{patient_info.get('species','Chien')}, {patient_info.get('weight','?')}kg.\n\n"
                        "Tu es cardiologue veterinaire expert en echocardiographie canine et feline.\n"
                        "Identifie precisement le type de coupe et le mode echographique visible sur cette image, "
                        "puis fais une observation diagnostique clinique courte et precise.\n"
                        "Reponds UNIQUEMENT en JSON (pas de markdown):\n"
                        '{"caption":"type de vue ex: Parasternale droite grand axe Mode B","comment":"observation clinique precise max 130 car"}'
                    )}
                ]
            }]
        )
        txt = msg.content[0].text if msg.content else ''
        clean = re.sub(r'```(?:json)?\n?', '', txt).replace('```', '').strip()
        match = re.search(r'\{[^{}]+\}', clean)
        if match:
            result = json.loads(match.group(0))
            return {
                'caption': result.get('caption', f'Vue {image_num+1}'),
                'comment': result.get('comment', '')
            }
        return {'caption': f'Vue echographique {image_num+1}', 'comment': ''}
    except Exception as e:
        print(f"Vision error {image_num+1}: {e}")
        return {'caption': f'Vue echographique {image_num+1}', 'comment': ''}

def build_images_html(images_b64, comments):
    if not images_b64:
        return ''
    html = (
        '<div style="background:#1a3a5c;color:white;padding:6px 12px;font-weight:bold;'
        'font-size:11px;margin:14px 0 6px">IMAGES ECHOGRAPHIQUES</div>'
        '<p style="font-size:9px;color:#6b7280;margin-bottom:10px">'
        'Vues echographiques (Esaote MyLab) — interpretation automatique par IA.</p>'
        '<table style="width:100%;border-collapse:collapse;margin-bottom:12px">'
    )
    for i in range(0, len(images_b64), 2):
        html += '<tr>'
        for j in range(2):
            idx = i + j
            if idx < len(images_b64):
                com = comments[idx] if idx < len(comments) else {}
                caption = com.get('caption', f'Vue {idx+1}')
                comment = com.get('comment', '')
                img_data = f'data:image/jpeg;base64,{images_b64[idx]}'
                html += (
                    f'<td style="width:50%;padding:6px;vertical-align:top;'
                    f'border:1px solid #e5e7eb;background:#f9fafb">'
                    f'<img src="{img_data}" style="width:100%;border-radius:3px 3px 0 0;display:block;'
                    f'border:1px solid #1a3a5c;border-bottom:none"/>'
                    f'<div style="background:#1a3a5c;color:white;font-size:9px;font-weight:bold;'
                    f'padding:4px 6px">{caption}</div>'
                    + (f'<div style="font-size:8px;color:#1e3a5f;padding:4px 6px;'
                       f'font-style:italic;line-height:1.5;background:white;border:1px solid #e5e7eb;border-top:none">'
                       f'&#x1F50D; {comment}</div>' if comment else '')
                    + '</td>'
                )
            else:
                html += '<td style="width:50%;padding:6px"></td>'
        html += '</tr>'
    html += '</table>'
    return html

@app.route('/generate-cr', methods=['POST', 'OPTIONS'])
def generate_cr():
    if request.method == 'OPTIONS':
        return '', 200
    try:
        data = request.get_json(force=True)
    except Exception as e:
        return jsonify({'error': 'Invalid JSON: ' + str(e)}), 400
    if not data:
        return jsonify({'error': 'No data'}), 400

    xml_data    = data.get('xmlData', '')
    patient     = data.get('patient', {})
    image_paths = data.get('imagePaths', [])
    file_path   = data.get('filePath', '')
    consult_id  = data.get('consultId', '')
    sb_url      = data.get('supabaseUrl', 'https://ddphqsmihbasndmautyu.supabase.co')
    sb_key      = data.get('supabaseKey', '')
    weight      = float(patient.get('weight', 0) or 0)
    bw_exp      = round(weight ** 0.294, 3) if weight > 0 else None

    def xm(key):
        r = re.search(r'Description="' + re.escape(key) + r'"[\s\S]*?<MeanValue[^>]*Value="([^"]+)"', xml_data)
        return float(r.group(1)) if r else None

    rm = {
        'LVIDd': xm('Diametre dias VG') or xm('DdVG'),
        'LVIDs': xm('Diametre sys VG') or xm('DsVG'),
        'IVSd':  xm('SIV diast'),
        'PPd':   xm('Diametre paroi post diast VG'),
        'FE':    xm("Fraction d'ejection"),
        'FR':    xm('Fraction de raccourcissement VG'),
        'FC':    xm('Frequence cardiaque'),
        'DC':    xm('Debit cardiaque'),
        'VmaxAo': abs(xm('Vmax Ao') or 0) or None,
        'GmaxAo': xm('Gmax Ao'),
        'VmaxAP': abs(xm('Vmax AP') or 0) or None,
        'VmaxIM': abs(xm('Vmax IM') or 0) or None,
        'OGAo':  xm('OG /Ao'),
        'EA':    xm('E/A VM'),
        'EeRatio': xm("E/e' Lat"),
        'PCP':   xm('Pression capillaire pulmonaire'),
    }
    if rm['LVIDd'] and weight and bw_exp:
        rm['LVIDdN'] = round(rm['LVIDd'] / (bw_exp * 10), 3)
    if rm['PPd'] and rm['LVIDd']:
        rm['EPR'] = round(2 * rm['PPd'] / rm['LVIDd'], 3)

    cornell = {k: round(c * bw_exp * 10, 1) if bw_exp else None
               for k, c in [('LVIDd', 1.53), ('LVIDs', 0.97), ('IVSd', 0.48), ('PPd', 0.48)]}

    # Télécharger et convertir les images BMP
    images_b64 = []
    paths_to_use = image_paths if image_paths else ([file_path] if file_path else [])
    print(f"Images to process: {len(paths_to_use)}")

    for path in paths_to_use[:12]:  # max 12 images
        if not path:
            continue
        img_bytes = download_image(sb_url, sb_key, path)
        if img_bytes:
            b64 = image_to_jpeg_b64(img_bytes, path)
            if b64:
                images_b64.append(b64)
                print(f"  OK: {path.split('/')[-1]}")
            else:
                print(f"  FAIL convert: {path}")
        else:
            print(f"  FAIL download: {path}")

    print(f"Images ready: {len(images_b64)}")

    key = CLAUDE_KEY
    if not key:
        return jsonify({'error': 'CLAUDE_API_KEY not set'}), 500

    # Analyser chaque image avec Claude Vision (max 8)
    comments = []
    for i, b64 in enumerate(images_b64[:8]):
        print(f"Vision {i+1}/{min(len(images_b64),8)}...")
        com = comment_single_image(b64, i, patient, key)
        comments.append(com)
        print(f"  -> {com.get('caption','?')[:60]}")

    # Générer le CR textuel
    prompt = (
        f"Tu es Dr Vet. Sebastien ROUL, cardiologue veterinaire (N 6603 OMV / MRCVS).\n"
        f"Patient: {patient.get('animalName','?')} {patient.get('species','Chien')}"
        f"{' / ' + patient.get('breed','') if patient.get('breed') else ''}, "
        f"{weight}kg (BW0.294={bw_exp})\n"
        f"Proprietaire: {patient.get('firstName','')} {patient.get('lastName','')}\n"
        f"Date: {patient.get('date','?')} | Clinique: {patient.get('clinic','ImagemVet')}\n\n"
        f"MESURES XML:\n{json.dumps({k:v for k,v in rm.items() if v is not None}, indent=1)}\n\n"
        f"VALEURS PREDITES CORNELL ({weight}kg):\n{json.dumps(cornell, indent=1)}\n\n"
        f"Genere un compte rendu echocardiographique professionnel et complet.\n"
        f"References: Chetboul et al. AJVR 2005 | Thomas et al. AJVR 1993 | ACVIM 2019.\n"
        f"Statuts: N=normal, L=limite (10-20%), A=anormal (>20%), C=critique.\n"
        f"IMPORTANT: signification max 80 caracteres.\n"
        f"Reponds UNIQUEMENT en JSON valide sans markdown:\n"
        '{{"mesures":{{"LVIDd":{{"val":null,"statut":"N","signification":"court"}},'
        '"LVIDs":{{"val":null,"statut":"N","signification":"court"}},'
        '"IVSd":{{"val":null,"statut":"N","signification":"court"}},'
        '"PPd":{{"val":null,"statut":"N","signification":"court"}},'
        '"LVIDdN":{{"val":null,"statut":"N","signification":"court"}},'
        '"EPR":{{"val":null,"statut":"N","signification":"court"}},'
        '"FE":{{"val":null,"statut":"N","signification":"court"}},'
        '"FR":{{"val":null,"statut":"N","signification":"court"}},'
        '"FC":{{"val":null,"statut":"N","signification":"court"}},'
        '"DC":{{"val":null,"statut":"N","signification":"court"}},'
        '"VmaxAo":{{"val":null,"statut":"N","signification":"court"}},'
        '"GmaxAo":{{"val":null,"statut":"N","signification":"court"}},'
        '"VmaxAP":{{"val":null,"statut":"N","signification":"court"}},'
        '"VmaxIM":{{"val":null,"statut":"N","signification":"court"}},'
        '"OGAo":{{"val":null,"statut":"N","signification":"court"}},'
        '"EA":{{"val":null,"statut":"N","signification":"court"}},'
        '"EeRatio":{{"val":null,"statut":"N","signification":"court"}},'
        '"PCP":{{"val":null,"statut":"N","signification":"court"}}}},'
        '"analyse":{{"systolique":"detail","diastolique":"detail","aorte":"detail","atrium":"detail","pulmonaire":"detail"}},'
        '"acvim":{{"stade":"A","description":"detail"}},'
        '"recommandations":{{"suivi":"detail","traitement":"detail","vigilance":"detail","elevage":""}},'
        '"conclusion":"conclusion complete"}}'
    )

    try:
        import anthropic as ac
        client_ai = ac.Anthropic(api_key=key)
        message = client_ai.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=4000,
            messages=[{'role': 'user', 'content': prompt}]
        )
        txt = message.content[0].text if message.content else ''
    except Exception as e:
        return jsonify({'error': 'Claude API error: ' + str(e)}), 500

    json_str = ''
    try:
        clean = txt.strip()
        if clean.startswith('```'):
            clean = re.sub(r'^```(?:json)?\n?', '', clean)
            clean = re.sub(r'\n?```$', '', clean)
            clean = clean.strip()
        match = re.search(r'\{[\s\S]*\}', clean)
        json_str = match.group(0) if match else clean
        json_str = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', json_str)
        report = json.loads(json_str)
    except Exception as e:
        try:
            simple = re.sub(r'"signification"\s*:\s*"[^"]*"', '"signification": "voir analyse"', json_str)
            report = json.loads(simple)
        except:
            return jsonify({'error': 'JSON parse error: ' + str(e), 'raw': txt[:500]}), 500

    for k, v in rm.items():
        if v is not None and k in report.get('mesures', {}):
            report['mesures'][k]['val'] = v

    images_html = build_images_html(images_b64[:8], comments)

    return jsonify({
        'success': True,
        'report': report,
        'images_html': images_html,
        'images_count': len(images_b64),
        'weight': weight,
        'bw_exp': bw_exp
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
