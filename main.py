import os, json, re, base64, io
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

CLAUDE_KEY = os.environ.get('CLAUDE_API_KEY', '')

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'CardioVet CR Generator v5', 'key_set': bool(CLAUDE_KEY)})

def download_pdf(sb_url, sb_key, file_path):
    try:
        import requests
        url = f"{sb_url}/storage/v1/object/cardiovet-files/{file_path}"
        r = requests.get(url, headers={'apikey': sb_key, 'Authorization': f'Bearer {sb_key}'}, timeout=30)
        return r.content if r.status_code == 200 else None
    except Exception as e:
        print(f"Download error: {e}")
        return None

def is_echo_image(img):
    gray = img.convert('L')
    pixels = list(gray.getdata())
    avg = sum(pixels) / len(pixels)
    light_pixels = sum(1 for p in pixels if p > 50)
    light_ratio = light_pixels / len(pixels)
    return light_ratio > 0.05 and avg > 5

def is_dark_page(img):
    gray = img.convert('L')
    pixels = list(gray.getdata())
    avg = sum(pixels) / len(pixels)
    dark_pixels = sum(1 for p in pixels if p < 80)
    dark_ratio = dark_pixels / len(pixels)
    print(f"  avg={avg:.0f}, dark_ratio={dark_ratio:.2f}")
    return avg < 130 and dark_ratio > 0.35

def split_page_into_quadrants(img):
    """Decoupe une page Esaote en 6 vues individuelles (grille 2x3)"""
    from PIL import Image
    w, h = img.size
    top_offset = int(h * 0.08)
    bottom_offset = int(h * 0.03)
    usable_h = h - top_offset - bottom_offset
    half_w = w // 2
    third_h = usable_h // 3

    views = []
    for row in range(3):
        for col in range(2):
            x1 = col * half_w
            y1 = top_offset + row * third_h
            x2 = x1 + half_w
            y2 = y1 + third_h
            views.append(img.crop((x1, y1, x2, y2)))
    return views

def extract_individual_views(pdf_bytes, max_pages=8):
    try:
        from pdf2image import convert_from_bytes
        from PIL import Image
        views_b64 = []
        pages = convert_from_bytes(pdf_bytes, dpi=150, first_page=1, last_page=max_pages, fmt='jpeg')

        for i, page in enumerate(pages):
            if not is_dark_page(page):
                print(f"Page {i+1}: texte ignoree")
                continue
            print(f"Page {i+1}: decoupage en vues individuelles")

            views = split_page_into_quadrants(page)
            for q_idx, view in enumerate(views):
                if not is_echo_image(view):
                    print(f"  Vue {q_idx+1}: vide, ignoree")
                    continue
                target_w = 800
                if view.width > target_w:
                    ratio = target_w / view.width
                    view = view.resize((target_w, int(view.height * ratio)), Image.LANCZOS)
                buf = io.BytesIO()
                view.save(buf, format='JPEG', quality=85)
                views_b64.append(base64.b64encode(buf.getvalue()).decode())
                print(f"  Vue {q_idx+1}: conservee ({view.width}x{view.height})")

        print(f"Total vues extraites: {len(views_b64)}")
        return views_b64
    except Exception as e:
        print(f"Extraction error: {e}")
        import traceback
        traceback.print_exc()
        return []

def comment_single_view(img_b64, view_num, patient_info, key):
    try:
        import anthropic as ac
        client_ai = ac.Anthropic(api_key=key)

        msg = client_ai.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=250,
            messages=[{
                'role': 'user',
                'content': [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}
                    },
                    {
                        "type": "text",
                        "text": (
                            f"Patient: {patient_info.get('animalName','?')}, "
                            f"{patient_info.get('species','Chien')}, {patient_info.get('weight','?')}kg.\n\n"
                            "Tu es cardiologue veterinaire expert en echocardiographie canine et feline.\n"
                            "Identifie le type de coupe/mode echographique et fais une observation diagnostique courte.\n"
                            "Reponds UNIQUEMENT en JSON (pas de markdown):\n"
                            '{"caption":"type de vue court ex: Parasternale droite grand axe Mode B","comment":"observation diagnostique 1 phrase max 120 car"}'
                        )
                    }
                ]
            }]
        )
        txt = msg.content[0].text if msg.content else ''
        clean = re.sub(r'```(?:json)?\n?', '', txt).replace('```', '').strip()
        match = re.search(r'\{[^{}]+\}', clean)
        if match:
            result = json.loads(match.group(0))
            return {
                'caption': result.get('caption', f'Vue {view_num+1}'),
                'comment': result.get('comment', '')
            }
        return {'caption': f'Vue echographique {view_num+1}', 'comment': ''}
    except Exception as e:
        print(f"Vision error vue {view_num+1}: {e}")
        return {'caption': f'Vue echographique {view_num+1}', 'comment': ''}

def build_images_html(views_b64, comments):
    if not views_b64:
        return ''
    html = (
        '<div style="background:#1a3a5c;color:white;padding:6px 12px;font-weight:bold;'
        'font-size:11px;margin:14px 0 6px">IMAGES ECHOGRAPHIQUES</div>'
        '<p style="font-size:9px;color:#6b7280;margin-bottom:10px">'
        'Vues echographiques individuelles (Esaote MyLab) — interpretation automatique par IA.</p>'
        '<table style="width:100%;border-collapse:collapse;margin-bottom:12px">'
    )
    for i in range(0, len(views_b64), 2):
        html += '<tr>'
        for j in range(2):
            idx = i + j
            if idx < len(views_b64):
                com = comments[idx] if idx < len(comments) else {}
                caption = com.get('caption', f'Vue {idx+1}')
                comment = com.get('comment', '')
                img_data = f'data:image/jpeg;base64,{views_b64[idx]}'
                html += (
                    f'<td style="width:50%;padding:6px;vertical-align:top;'
                    f'border:1px solid #e5e7eb;background:#f9fafb">'
                    f'<img src="{img_data}" style="width:100%;border-radius:3px;display:block;'
                    f'border:1px solid #1a3a5c"/>'
                    f'<div style="background:#1a3a5c;color:white;font-size:9px;font-weight:bold;'
                    f'padding:3px 6px;margin-top:0;border-radius:0 0 3px 3px">{caption}</div>'
                    + (
                        f'<div style="font-size:8px;color:#1e3a5f;margin-top:4px;'
                        f'font-style:italic;line-height:1.5;padding:0 2px">'
                        f'&#x1F50D; {comment}</div>' if comment else ''
                    )
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

    xml_data   = data.get('xmlData', '')
    patient    = data.get('patient', {})
    file_path  = data.get('filePath', '')
    consult_id = data.get('consultId', '')
    sb_url     = data.get('supabaseUrl', 'https://ddphqsmihbasndmautyu.supabase.co')
    sb_key     = data.get('supabaseKey', '')
    weight     = float(patient.get('weight', 0) or 0)
    bw_exp     = round(weight ** 0.294, 3) if weight > 0 else None

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

    views_b64 = []
    if file_path and sb_key:
        pdf_bytes = download_pdf(sb_url, sb_key, file_path)
        if pdf_bytes:
            print(f"PDF: {len(pdf_bytes)} bytes")
            views_b64 = extract_individual_views(pdf_bytes, max_pages=10)

    key = CLAUDE_KEY
    if not key:
        return jsonify({'error': 'CLAUDE_API_KEY not set'}), 500

    comments = []
    for i, v_b64 in enumerate(views_b64[:8]):
        print(f"Vision image {i+1}/{min(len(views_b64),8)}...")
        com = comment_single_view(v_b64, i, patient, key)
        comments.append(com)
        print(f"  {com.get('caption','?')[:50]}")

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

    images_html = build_images_html(views_b64[:8], comments)

    return jsonify({
        'success': True,
        'report': report,
        'images_html': images_html,
        'images_count': len(views_b64),
        'weight': weight,
        'bw_exp': bw_exp
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
