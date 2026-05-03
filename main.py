import os, json, re
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

CLAUDE_KEY = os.environ.get('CLAUDE_API_KEY', '')

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'CardioVet CR Generator', 'key_set': bool(CLAUDE_KEY)})

@app.route('/generate-cr', methods=['POST', 'OPTIONS'])
def generate_cr():
    if request.method == 'OPTIONS':
        return '', 200

    try:
        data = request.get_json(force=True)
    except Exception as e:
        return jsonify({'error': 'Invalid JSON: ' + str(e)}), 400

    if not data:
        return jsonify({'error': 'No data received'}), 400

    xml_data = data.get('xmlData', '')
    patient  = data.get('patient', {})
    weight   = float(patient.get('weight', 0) or 0)
    bw_exp   = round(weight ** 0.294, 3) if weight > 0 else None

    def xm(key):
        r = re.search(r'Description="' + re.escape(key) + r'"[\s\S]*?<MeanValue[^>]*Value="([^"]+)"', xml_data)
        return float(r.group(1)) if r else None

    rm = {
        'LVIDd': xm('Diamètre dias VG') or xm('DdVG'),
        'LVIDs': xm('Diamètre sys VG') or xm('DsVG'),
        'IVSd':  xm('SIV diast'),
        'PPd':   xm('Diamètre paroi post diast VG'),
        'FE':    xm("Fraction d'éjection"),
        'FR':    xm('Fraction de raccourcissement VG'),
        'FC':    xm('Fréquence cardiaque'),
        'DC':    xm('Débit cardiaque'),
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

    key = CLAUDE_KEY
    if not key:
        return jsonify({'error': 'CLAUDE_API_KEY not set on server'}), 500

    prompt = (
        f"Tu es Dr Vét. Sébastien ROUL, cardiologue vétérinaire (N° 6603 OMV / MRCVS).\n"
        f"Patient: {patient.get('animalName','?')} {patient.get('species','Chien')}"
        f"{' / ' + patient.get('breed','') if patient.get('breed') else ''}, "
        f"{weight}kg (BW^0,294={bw_exp})\n"
        f"Propriétaire: {patient.get('firstName','')} {patient.get('lastName','')}\n"
        f"Date: {patient.get('date','?')} | Clinique: {patient.get('clinic','ImagemVet')}\n\n"
        f"MESURES XML:\n{json.dumps({k:v for k,v in rm.items() if v is not None}, indent=1)}\n\n"
        f"VALEURS PRÉDITES CORNELL ({weight}kg):\n{json.dumps(cornell, indent=1)}\n\n"
        f"Génère un compte rendu échocardiographique professionnel complet.\n"
        f"Statuts: N=normal, L=limite (10-20% hors norme), A=anormal (>20%).\n"
        f"Réponds UNIQUEMENT en JSON valide sans markdown:\n"
        f'{{"mesures":{{"LVIDd":{{"val":null,"statut":"N","signification":"..."}},'
        f'"LVIDs":{{"val":null,"statut":"N","signification":"..."}},'
        f'"IVSd":{{"val":null,"statut":"N","signification":"..."}},'
        f'"PPd":{{"val":null,"statut":"N","signification":"..."}},'
        f'"LVIDdN":{{"val":null,"statut":"N","signification":"..."}},'
        f'"EPR":{{"val":null,"statut":"N","signification":"..."}},'
        f'"FE":{{"val":null,"statut":"N","signification":"..."}},'
        f'"FR":{{"val":null,"statut":"N","signification":"..."}},'
        f'"FC":{{"val":null,"statut":"N","signification":"..."}},'
        f'"DC":{{"val":null,"statut":"N","signification":"..."}},'
        f'"VmaxAo":{{"val":null,"statut":"N","signification":"..."}},'
        f'"GmaxAo":{{"val":null,"statut":"N","signification":"..."}},'
        f'"VmaxAP":{{"val":null,"statut":"N","signification":"..."}},'
        f'"VmaxIM":{{"val":null,"statut":"N","signification":"..."}},'
        f'"OGAo":{{"val":null,"statut":"N","signification":"..."}},'
        f'"EA":{{"val":null,"statut":"N","signification":"..."}},'
        f'"EeRatio":{{"val":null,"statut":"N","signification":"..."}},'
        f'"PCP":{{"val":null,"statut":"N","signification":"..."}}}},'
        f'"analyse":{{"systolique":"...","diastolique":"...","aorte":"...","atrium":"...","pulmonaire":"..."}},'
        f'"acvim":{{"stade":"A","description":"..."}},'
        f'"recommandations":{{"suivi":"...","traitement":"...","vigilance":"...","elevage":""}},'
        f'"conclusion":"..."}}'
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

        try:
        # Retirer les balises markdown si présentes
        clean = txt.strip()
        if clean.startswith('```'):
            clean = re.sub(r'^```(?:json)?\n?', '', clean)
            clean = re.sub(r'\n?```$', '', clean)
            clean = clean.strip()
        match = re.search(r'\{[\s\S]*\}', clean)
        report = json.loads(match.group(0) if match else clean)
    except Exception as e:
        return jsonify({'error': 'JSON parse error: ' + str(e), 'raw': txt[:500]}),

    for k, v in rm.items():
        if v is not None and k in report.get('mesures', {}):
            report['mesures'][k]['val'] = v

    return jsonify({'success': True, 'report': report, 'weight': weight, 'bw_exp': bw_exp})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
