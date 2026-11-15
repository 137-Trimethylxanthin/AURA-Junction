import type { Lang } from '@/api/contract'

/**
 * Titles of reasoning-tree steps arrive from the backend in English. The fixed ones are shown in the
 * interface language; a title that is not in this table (a tool name, a model's own wording) stays as sent.
 */
const TITLES: Record<string, Partial<Record<Lang, string>>> = {
  Question: { de: 'Frage', fr: 'Question', fi: 'Kysymys', it: 'Domanda' },
  Intent: { de: 'Absicht', fr: 'Intention', fi: 'Tarkoitus', it: 'Intento' },
  Scope: { de: 'Geltungsbereich', fr: 'Périmètre', fi: 'Rajaus', it: 'Ambito' },
  Answer: { de: 'Antwort', fr: 'Réponse', fi: 'Vastaus', it: 'Risposta' },
  'Pick the best source': { de: 'Beste Quelle wählen', fr: 'Choisir la meilleure source', fi: 'Paras lähde', it: 'Scelta della fonte migliore' },
  'Read the layer': { de: 'Ebene lesen', fr: 'Lire le calque', fi: 'Tason luku', it: 'Lettura del layer' },
  'Read the image': { de: 'Bild lesen', fr: 'Lire l’image', fi: 'Kuvan luku', it: 'Lettura dell’immagine' },
  'Read the drawing’s record': { de: 'Datensatz der Zeichnung lesen', fr: 'Lire la fiche du dessin', fi: 'Piirustuksen tietojen luku', it: 'Lettura dei dati del disegno' },
  'Read the printed dimensions': { de: 'Gedruckte Maße lesen', fr: 'Lire les cotes imprimées', fi: 'Painettujen mittojen luku', it: 'Lettura delle quote stampate' },
  'Cross-check on the image': { de: 'Gegenprüfung am Bild', fr: 'Contre-vérification sur l’image', fi: 'Ristiintarkistus kuvasta', it: 'Controllo incrociato sull’immagine' },
  'Collect rooms': { de: 'Räume sammeln', fr: 'Rassembler les pièces', fi: 'Huoneiden keruu', it: 'Raccolta dei locali' },
  'Tool call': { de: 'Werkzeugaufruf', fr: 'Appel d’outil', fi: 'Työkalukutsu', it: 'Chiamata allo strumento' },
  'Semantic cache match': { de: 'Treffer im semantischen Cache', fr: 'Correspondance du cache sémantique', fi: 'Osuma semanttisessa välimuistissa', it: 'Corrispondenza nella cache semantica' },
  'Provenance filter': { de: 'Herkunftsfilter', fr: 'Filtre de provenance', fi: 'Alkuperäsuodatin', it: 'Filtro di provenienza' },
  'Measured on the drawing': { de: 'An der Zeichnung gemessen', fr: 'Mesuré sur le dessin', fi: 'Mitattu piirustuksesta', it: 'Misurato sul disegno' },
  'Look for the data': { de: 'Daten suchen', fr: 'Chercher les données', fi: 'Tietojen haku', it: 'Ricerca dei dati' },
  'Inspect the drawing': { de: 'Zeichnung prüfen', fr: 'Examiner le dessin', fi: 'Piirustuksen tarkastelu', it: 'Esame del disegno' },
  'Inspecting marked reference region': { de: 'Markierten Bereich prüfen', fr: 'Examen de la zone marquée', fi: 'Merkityn alueen tarkastelu', it: 'Esame dell’area segnata' },
  'Find printed text': { de: 'Gedruckten Text finden', fr: 'Trouver le texte imprimé', fi: 'Painetun tekstin haku', it: 'Ricerca del testo stampato' },
  'Candidate scan': { de: 'Kandidatensuche', fr: 'Recherche de candidats', fi: 'Ehdokkaiden haku', it: 'Ricerca dei candidati' },
  'Candidate verification': { de: 'Kandidatenprüfung', fr: 'Vérification des candidats', fi: 'Ehdokkaiden tarkistus', it: 'Verifica dei candidati' },
  'Boxes found on the image': { de: 'Im Bild gefundene Rahmen', fr: 'Cadres trouvés sur l’image', fi: 'Kuvasta löydetyt kehykset', it: 'Riquadri trovati sull’immagine' },
  'Apply the scope': { de: 'Geltungsbereich anwenden', fr: 'Appliquer le périmètre', fi: 'Rajauksen käyttö', it: 'Applicazione dell’ambito' },
  'Apply your what-if size': { de: 'Ihre Was-wäre-wenn-Größe anwenden', fr: 'Appliquer votre taille hypothétique', fi: 'Oman kokeilukoon käyttö', it: 'Applicazione della dimensione ipotetica' },
  'Agent verification note': { de: 'Prüfnotiz des Agenten', fr: 'Note de vérification de l’agent', fi: 'Agentin tarkistushuomio', it: 'Nota di verifica dell’agente' },
  'Agent measurement method': { de: 'Messmethode des Agenten', fr: 'Méthode de mesure de l’agent', fi: 'Agentin mittaustapa', it: 'Metodo di misura dell’agente' },
}

/** `Route decision · gemini-decision-1` keeps its model name. */
const PREFIXED: Record<string, Partial<Record<Lang, string>>> = {
  'Route decision': { de: 'Routenentscheidung', fr: 'Décision de routage', fi: 'Reittipäätös', it: 'Decisione di instradamento' },
}

export function traceTitle(title: string, lang: Lang): string {
  if (lang === 'en') return title
  const hit = TITLES[title]?.[lang]
  if (hit) return hit
  const [head, ...rest] = title.split(' · ')
  const pre = PREFIXED[head]?.[lang]
  return pre ? [pre, ...rest].join(' · ') : title
}
