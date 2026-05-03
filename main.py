import os, json, base64, re, tempfile
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import anthropic

app = Flask(__name__)
CORS(app)

CLAUDE_KEY = os.environ.get('CLAUDE_API_KEY', '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')

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'CardioVet CR Generator'})

@app.route('/generate-cr', methods=['POST', 'OPTIONS'])
def generate_cr():
    if request.method == 'OPTIONS':
        return '', 200

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400

    xml_data    = data.get('xmlData', '')
    patient     = data.get('patient', {})
    weight      = float(patient.get('weight', 0) or 0)
    bw_exp      = round(weight ** 0.294, 3) if weight > 0 else None

    # ── Extraire les mesures du XML ─────────────────────────────────
    def xm(xml, key):
        r = re.search(f'Description="{re.escape(key)}"[\\s\\S]*?<MeanValue[^>]*Value="([^"]+)"', xml)
        return float(r.group(1)) if r else None

    rm = {
        'LVIDd': xm(xml_data, 'Diamètre dias VG') or xm(xml_data, 'DdVG'),
        'LVIDs': xm(xml_data, 'Diamètre sys VG') or xm(xml_data, 'DsVG'),
        'IVSd':  xm(xml_data, 'SIV diast'),
        'PPd':   xm(xml_data, 'Diamètre paroi post diast VG'),
        'IVSs':  xm(xml_data, 'SIV sys'),
        'PPs':   xm(xml_data, 'Diamètre paroi post syst VG'),
        'FE':    xm(xml_data, "Fraction d'éjection"),
        'FR':    xm(xml_data, 'Fraction de raccourcissement VG'),
        'FC':    xm(xml_data, 'Fréquence cardiaque'),
        'DC':    xm(xml_data, 'Débit cardiaque'),
        'VmaxAo': abs(xm(xml_data, 'Vmax Ao') or 0) or None,
        'GmaxAo': xm(xml_data, 'Gmax Ao'),
        'VmaxAP': abs(xm(xml_data, 'Vmax AP') or 0) or None,
        'GmaxAP': xm(xml_data, 'Gmax AP'),
        'VmaxIM': abs(xm(xml_data, 'Vmax IM') or 0) or None,
        'GmaxIM': xm(xml_data, 'Gmax IM'),
        'OGAo':  xm(xml_data, 'OG /Ao'),
        'EA':    xm(xml_data, 'E/A VM'),
        'EeRatio': xm(xml_data, "E/e' Lat"),
        'PCP':   xm(xml_data, 'Pression capillaire pulmonaire'),
        'DdAo':  xm(xml_data, 'Diamètre aortique'),
        'DOG':   xm(xml_data, 'Diamètre OG'),
    }

    # Calculs dérivés
    if rm['LVIDd'] and weight:
        rm['LVIDdN'] = round(rm['LVIDd'] / (bw_exp * 10), 3)
    if rm['PPd'] and rm['LVIDd']:
        rm['EPR'] = round(2 * rm['PPd'] / rm['LVIDd'], 3)

    # Valeurs prédites Cornell
    cornell = {
        'LVIDd': round(1.53 * bw_exp * 10, 1) if bw_exp else None,
        'LVIDs': round(0.97 * bw_exp * 10, 1) if bw_exp else None,
        'IVSd':  round(0.48 * bw_exp * 10, 1) if bw_exp else None,
        'PPd':   round(0.48 * bw_exp * 10, 1) if bw_exp else None,
    }

    # ── Appel Claude Sonnet (pas de limite de temps sur Railway) ────
    client_ai = anthropic.Anthropic(api_key=CLAUDE_KEY)

    prompt = f"""Tu es le Dr Vét. Sébastien ROUL, cardiologue vétérinaire itinérant (N° 6603 OMV / MRCVS), spécialiste en échocardiographie canine et féline.

PATIENT : {patient.get('animalName', '?')} — {patient.get('species', 'Chien')}{(' / ' + patient.get('breed', '')) if patient.get('breed') else ''}
PROPRIÉTAIRE : {patient.get('firstName', '')} {patient.get('lastName', '')}
POIDS : {weight} kg (BW^0,294 = {bw_exp})
DATE D'EXAMEN : {patient.get('date', '?')}
CLINIQUE : {patient.get('clinic', 'ImagemVet Cardiologie EchoDoppler Itinérante')}

MESURES ÉCHOGRAPHIQUES EXTRAITES DU XML :
{json.dumps(rm, indent=2, ensure_ascii=False)}

VALEURS PRÉDITES CORNELL ({weight} kg) :
{json.dumps(cornell, indent=2)}

RÉFÉRENCES DOPPLER (Chetboul et al., AJVR 2005 ; 66 : 953-961) :
- E/A mitral : 1,46 ± 0,35 (1,11 – 1,81)
- E/A tricuspidien : 1,75 ± 0,34
- Vmax Ao ≤ 1,70 m/s | Vmax AP < 1,60 m/s
- OG/Ao < 1,5 | LVIDdN < 1,70

Génère un compte rendu échocardiographique complet et professionnel.
Pour chaque mesure : statut N=normal, L=limite (10-20% hors norme), A=anormal (>20% hors norme), C=critique.
Interprète cliniquement chaque anomalie avec sa signification physiopathologique.

Réponds UNIQUEMENT en JSON valide (sans markdown, sans backticks) :
{{
  "mesures": {{
    "LVIDd": {{"val": null, "pred": {cornell.get('LVIDd')}, "ecart_pct": null, "statut": "N", "signification": "..."}},
    "LVIDs": {{"val": null, "pred": {cornell.get('LVIDs')}, "ecart_pct": null, "statut": "N", "signification": "..."}},
    "IVSd":  {{"val": null, "pred": {cornell.get('IVSd')},  "ecart_pct": null, "statut": "N", "signification": "..."}},
    "PPd":   {{"val": null, "pred": {cornell.get('PPd')},   "ecart_pct": null, "statut": "N", "signification": "..."}},
    "LVIDdN":{{"val": null, "statut": "N", "signification": "..."}},
    "EPR":   {{"val": null, "statut": "N", "signification": "..."}},
    "FE":    {{"val": null, "statut": "N", "signification": "..."}},
    "FR":    {{"val": null, "statut": "N", "signification": "..."}},
    "FC":    {{"val": null, "statut": "N", "signification": "..."}},
    "DC":    {{"val": null, "statut": "N", "signification": "..."}},
    "VmaxAo":{{"val": null, "statut": "N", "signification": "..."}},
    "GmaxAo":{{"val": null, "statut": "N", "signification": "..."}},
    "VmaxAP":{{"val": null, "statut": "N", "signification": "..."}},
    "VmaxIM":{{"val": null, "statut": "N", "signification": "..."}},
    "OGAo":  {{"val": null, "statut": "N", "signification": "..."}},
    "EA":    {{"val": null, "statut": "N", "signification": "..."}},
    "EeRatio":{{"val": null, "statut": "N", "signification": "..."}},
    "PCP":   {{"val": null, "statut": "N", "signification": "..."}}
  }},
  "analyse": {{
    "systolique": "texte détaillé analyse systolique VG avec interprétation clinique",
    "diastolique": "texte détaillé analyse diastolique et pressions de remplissage",
    "aorte": "texte détaillé analyse flux aortique avec classification SAS si applicable",
    "atrium": "texte détaillé analyse atriale gauche",
    "pulmonaire": "texte détaillé circulation pulmonaire et régurgitations"
  }},
  "acvim": {{
    "stade": "A",
    "description": "texte complet et détaillé de la classification ACVIM avec justification clinique"
  }},
  "recommandations": {{
    "suivi": "délai précis et modalités de suivi avec justification",
    "traitement": "indication médicamenteuse précise ou absence avec justification",
    "vigilance": "signes d'alarme cliniques à surveiller",
    "elevage": "conseil élevage/reproduction si applicable"
  }},
  "conclusion": "texte complet de la conclusion diagnostique, synthétique mais détaillé, incluant le diagnostic principal, son grade/stade, et la signification clinique pour le propriétaire"
}}"""

    message = client_ai.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=4000,
        messages=[{'role': 'user', 'content': prompt}]
    )

    txt = message.content[0].text if message.content else ''

    # Nettoyer et parser
    clean = re.sub(r'```json\n?', '', txt).replace('```', '').strip()
    match = re.search(r'\{[\s\S]*\}', clean)
    report = json.loads(match.group(0) if match else clean)

    # Fusionner valeurs XML avec interprétations Claude
    for k, v in rm.items():
        if v is not None and k in report.get('mesures', {}):
            report['mesures'][k]['val'] = v

    return jsonify({
        'success': True,
        'report': report,
        'measures_raw': rm,
        'cornell': cornell,
        'weight': weight,
        'bw_exp': bw_exp
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
