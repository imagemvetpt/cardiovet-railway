import os, json, re, base64, io
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

CLAUDE_KEY = os.environ.get('CLAUDE_API_KEY', '')

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'CardioVet CR Generator v9', 'key_set': bool(CLAUDE_KEY)})

def download_file(sb_url, sb_key, file_path):
    try:
        import requests
        url = f"{sb_url}/storage/v1/object/cardiovet-files/{file_path}"
        r = requests.get(url, headers={'apikey': sb_key, 'Authorization': f'Bearer {sb_key}'}, timeout=30)
        return r.content if r.status_code == 200 else None
    except Exception as e:
        print(f"Download error {file_path}: {e}")
        return None

def extract_pdf_text(pdf_bytes):
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        text = ''
        for page in reader.pages:
            text += page.extract_text() + '\n'
        return text.strip()
    except Exception as e:
        print(f"PDF text extraction error: {e}")
        return ''

def image_to_jpeg_b64(img_bytes, filename=''):
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes))
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
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

def calculate_statut(key, value, cornell_pred=None):
    if value is None:
        return 'N'
    try:
        v = float(value)
    except:
        return 'N'

    if key in ('LVIDd', 'LVIDs', 'IVSd', 'PPd') and cornell_pred:
        try:
            pred = float(cornell_pred)
            ecart = (v - pred) / pred
            if abs(ecart) > 0.20: return 'A'
            if abs(ecart) > 0.10: return 'L'
            return 'N'
        except:
            return 'N'

    rules = {
        'LVIDdN': [('>=', 1.70, 'A'), ('>=', 1.60, 'L')],
        'EPR':    [('>=', 0.42, 'A'), ('>=', 0.38, 'L')],
        'FE':     [('<',  40.0, 'A'), ('<',  50.0, 'L')],
        'FR':     [('<',  20.0, 'A'), ('<',  25.0, 'L'), ('>', 50.0, 'A'), ('>', 44.0, 'L')],
        'FC':     [('<',  50.0, 'A'), ('<',  60.0, 'L'), ('>', 160.0,'A'), ('>', 130.0,'L')],
        'VmaxAo': [('>=', 2.50, 'A'), ('>=', 1.70, 'L')],
        'GmaxAo': [('>=', 36.0, 'A'), ('>=', 16.0, 'L')],
        'VmaxAP': [('>=', 2.00, 'A'), ('>=', 1.60, 'L')],
        'VmaxIM': [('>=', 5.00, 'A'), ('>=', 3.00, 'L')],
        'OGAo':   [('>=', 1.60, 'A'), ('>=', 1.50, 'L')],
        'EA':     [('<',  0.80, 'L'), ('<',  0.50, 'A'), ('>', 2.50, 'L'), ('>', 3.00, 'A')],
        'EeRatio':[('>=', 15.0, 'A'), ('>=', 10.0, 'L')],
        'PCP':    [('>=', 25.0, 'A'), ('>=', 15.0, 'L')],
    }
    if key in rules:
        for op, threshold, statut in rules[key]:
            if op == '>=' and v >= threshold: return statut
            if op == '<'  and v <  threshold: return statut
            if op == '>'  and v >  threshold: return statut
    return 'N'

def comment_single_image(img_b64, image_num, patient_info, key):
    try:
        import anthropic as ac
        client_ai = ac.Anthropic(api_key=key)
        msg = client_ai.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=300,
            messages=[{
                'role': 'user',
                'content': [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                    {"type": "text", "text": (
                        f"Patient: {patient_info.get('animalName','?')}, "
                        f"{patient_info.get('species','Chien')}, {patient_info.get('weight','?')}kg.\n\n"
                        "Tu es cardiologue veterinaire expert en echocardiographie canine et feline.\n"
                        "Analyse cette image echographique et reponds UNIQUEMENT en JSON (pas de markdown):\n"
                        '{"caption":"type de vue precis ex: Parasternale droite grand axe Mode B + CFM",'
                        '"comment":"observation clinique precise 1-2 phrases max 150 car avec valeurs visibles",'
                        '"statut":"N"}\n'
                        'statut: N=normal, A=anomalie detectee, S=suspect a surveiller'
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
                'comment': result.get('comment', ''),
                'statut': result.get('statut', 'N')
            }
        return {'caption': f'Vue echographique {image_num+1}', 'comment': '', 'statut': 'N'}
    except Exception as e:
        print(f"Vision error {image_num+1}: {e}")
        return {'caption': f'Vue echographique {image_num+1}', 'comment': '', 'statut': 'N'}

def build_images_html(images_b64, comments):
    if not images_b64:
        return ''
    def bc(s): return '#dc2626' if s=='A' else '#d97706' if s=='S' else '#1a3a5c'
    html = (
        '<div style="background:#1a3a5c;color:white;padding:6px 12px;font-weight:bold;'
        'font-size:11px;margin:14px 0 6px">IMAGES ECHOGRAPHIQUES</div>'
        '<p style="font-size:9px;color:#6b7280;margin-bottom:4px">'
        'Vues echographiques (Esaote MyLab) — interpretation par IA. '
        '<span style="color:#1a3a5c;font-weight:bold">&#9632; Normal</span> '
        '<span style="color:#d97706;font-weight:bold">&#9632; Suspect</span> '
        '<span style="color:#dc2626;font-weight:bold">&#9632; Anomalie</span></p>'
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
                statut  = com.get('statut', 'N')
                color   = bc(statut)
                img_data = f'data:image/jpeg;base64,{images_b64[idx]}'
                html += (
                    f'<td style="width:50%;padding:6px;vertical-align:top">'
                    f'<div style="border:2px solid {color};border-radius:4px;overflow:hidden">'
                    f'<img src="{img_data}" style="width:100%;display:block"/>'
                    f'<div style="background:{color};color:white;font-size:9px;font-weight:bold;'
                    f'padding:4px 8px;line-height:1.3">{caption}</div>'
                    + (f'<div style="font-size:8px;color:#374151;padding:5px 8px;'
                       f'font-style:italic;line-height:1.5;background:white">'
                       f'&#x1F50D; {comment}</div>' if comment else '')
                    + '</div></td>'
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

    xml_data       = data.get('xmlData', '')
    patient        = data.get('patient', {})
    image_paths    = data.get('imagePaths', [])
    file_path      = data.get('filePath', '')
    notes_pdf_path = data.get('notesPdfPath', '')
    consult_id     = data.get('consultId', '')
    sb_url         = data.get('supabaseUrl', 'https://ddphqsmihbasndmautyu.supabase.co')
    sb_key         = data.get('supabaseKey', '')
    weight         = float(patient.get('weight', 0) or 0)
    bw_exp         = round(weight ** 0.294, 3) if weight > 0 else None

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

    statuts = {}
    for k, v in rm.items():
        pred = cornell.get(k) if k in ('LVIDd','LVIDs','IVSd','PPd') else None
        statuts[k] = calculate_statut(k, v, pred)

    notes_text = ''
    if notes_pdf_path and sb_key:
        pdf_bytes = download_file(sb_url, sb_key, notes_pdf_path)
        if pdf_bytes:
            notes_text = extract_pdf_text(pdf_bytes)
            print(f"Notes PDF: {len(notes_text)} car")

    images_b64 = []
    paths_to_use = image_paths if image_paths else ([file_path] if file_path else [])
    for path in paths_to_use[:16]:
        if not path: continue
        img_bytes = download_file(sb_url, sb_key, path)
        if img_bytes:
            b64 = image_to_jpeg_b64(img_bytes, path)
            if b64:
                images_b64.append(b64)
                print(f"  OK: {path.split('/')[-1]}")
    print(f"Images: {len(images_b64)}")

    key = CLAUDE_KEY
    if not key:
        return jsonify({'error': 'CLAUDE_API_KEY not set'}), 500

    MAX_VISION = 10
    comments = []
    for i, b64 in enumerate(images_b64[:MAX_VISION]):
        print(f"Vision {i+1}/{min(len(images_b64),MAX_VISION)}...")
        com = comment_single_image(b64, i, patient, key)
        comments.append(com)
        print(f"  [{com.get('statut','?')}] {com.get('caption','?')[:55]}")
    for i in range(MAX_VISION, len(images_b64)):
        comments.append({'caption': f'Vue echographique {i+1}', 'comment': '', 'statut': 'N'})

    notes_section = ''
    if notes_text:
        notes_section = f"\nOBSERVATIONS CLINIQUES DU CARDIOLOGUE (priorite absolue):\n{notes_text}\n"

    prompt = (
        f"Tu es Dr Vet. Sebastien ROUL, cardiologue veterinaire (N 6603 OMV / MRCVS).\n"
        f"Patient: {patient.get('animalName','?')} {patient.get('species','Chien')}"
        f"{' / ' + patient.get('breed','') if patient.get('breed') else ''}, "
        f"{weight}kg (BW^0,294={bw_exp})\n"
        f"Proprietaire: {patient.get('firstName','')} {patient.get('lastName','')}\n"
        f"Date: {patient.get('date','?')} | Clinique: {patient.get('clinic','ImagemVet')}\n\n"
        f"MESURES XML:\n{json.dumps({k:v for k,v in rm.items() if v is not None}, indent=1)}\n\n"
        f"STATUTS CALCULES (utilise EXACTEMENT ces statuts):\n{json.dumps(statuts, indent=1)}\n\n"
        f"VALEURS PREDITES CORNELL ({weight}kg):\n{json.dumps(cornell, indent=1)}\n"
        + notes_section
        + f"\nOBSERVATIONS IMAGES IA:\n"
        + '\n'.join([f"- [{c.get('statut','N')}] {c.get('caption','')}: {c.get('comment','')}"
                     for c in comments if c.get('comment')])
        + f"\n\nGenere un compte rendu COMPLET et DETAILLE. Sections analyse: 3-5 phrases.\n"
        f"{'Integre les observations cliniques en priorite.' if notes_text else ''}\n"
        f"References: Chetboul AJVR 2005 | Thomas AJVR 1993 | ACVIM 2019 | Bussadori 2000.\n"
        f"Signification par mesure: max 90 car. Reponds UNIQUEMENT en JSON valide sans markdown:\n"
        '{{"mesures":{{"LVIDd":{{"val":null,"statut":"N","signification":"texte"}},'
        '"LVIDs":{{"val":null,"statut":"N","signification":"texte"}},'
        '"IVSd":{{"val":null,"statut":"N","signification":"texte"}},'
        '"PPd":{{"val":null,"statut":"N","signification":"texte"}},'
        '"LVIDdN":{{"val":null,"statut":"N","signification":"texte"}},'
        '"EPR":{{"val":null,"statut":"N","signification":"texte"}},'
        '"FE":{{"val":null,"statut":"N","signification":"texte"}},'
        '"FR":{{"val":null,"statut":"N","signification":"texte"}},'
        '"FC":{{"val":null,"statut":"N","signification":"texte"}},'
        '"DC":{{"val":null,"statut":"N","signification":"texte"}},'
        '"VmaxAo":{{"val":null,"statut":"N","signification":"texte"}},'
        '"GmaxAo":{{"val":null,"statut":"N","signification":"texte"}},'
        '"VmaxAP":{{"val":null,"statut":"N","signification":"texte"}},'
        '"VmaxIM":{{"val":null,"statut":"N","signification":"texte"}},'
        '"OGAo":{{"val":null,"statut":"N","signification":"texte"}},'
        '"EA":{{"val":null,"statut":"N","signification":"texte"}},'
        '"EeRatio":{{"val":null,"statut":"N","signification":"texte"}},'
        '"PCP":{{"val":null,"statut":"N","signification":"texte"}}}},'
        '"analyse":{{"systolique":"3-5 phrases","diastolique":"3-5 phrases",'
        '"aorte":"3-5 phrases avec SAS","atrium":"2-3 phrases","pulmonaire":"2-3 phrases"}},'
        '"acvim":{{"stade":"A","description":"3-4 phrases"}},'
        '"recommandations":{{"suivi":"precis","traitement":"precis","vigilance":"precis","elevage":""}},'
        '"conclusion":"4-6 phrases"}}'
    )

    try:
        import anthropic as ac
        client_ai = ac.Anthropic(api_key=key)
        message = client_ai.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=5000,
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

    # Forcer statuts calculés + valeurs XML
    for k, v in rm.items():
        if v is not None and k in report.get('mesures', {}):
            report['mesures'][k]['val'] = v
            report['mesures'][k]['statut'] = statuts.get(k, 'N')

    images_html = build_images_html(images_b64, comments)

    return jsonify({
        'success': True,
        'report': report,
        'images_html': images_html,
        'images_count': len(images_b64),
        'notes_loaded': bool(notes_text),
        'weight': weight,
        'bw_exp': bw_exp
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
