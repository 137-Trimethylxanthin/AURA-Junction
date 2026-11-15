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

type Tr = Partial<Record<Lang, string>>

/** Whole step sentences the backend sends as fixed text. */
const BODIES: Record<string, Tr> = {
  'The CAD geometry of this drawing answers counts and areas exactly, so no image model is needed for the answer.': {
    de: 'Die CAD-Geometrie dieser Zeichnung beantwortet Zählungen und Flächen exakt; für die Antwort ist kein Bildmodell nötig.',
    fr: 'La géométrie CAO de ce dessin répond exactement aux comptages et aux surfaces ; aucun modèle d’image n’est nécessaire.',
    fi: 'Tämän piirustuksen CAD-geometria vastaa lukumääriin ja pinta-aloihin tarkasti, joten kuvamallia ei tarvita.',
    it: 'La geometria CAD di questo disegno risponde in modo esatto a conteggi e superfici: non serve alcun modello d’immagine.',
  },
  'The IFC model\'s elements and stored quantities answer counts and areas exactly, so no image model is needed for the answer.': {
    de: 'Die Elemente und gespeicherten Mengen des IFC-Modells beantworten Zählungen und Flächen exakt; kein Bildmodell nötig.',
    fr: 'Les éléments et les quantités du modèle IFC répondent exactement aux comptages et aux surfaces ; aucun modèle d’image n’est nécessaire.',
    fi: 'IFC-mallin elementit ja tallennetut määrät vastaavat lukumääriin ja pinta-aloihin tarkasti, joten kuvamallia ei tarvita.',
    it: 'Gli elementi e le quantità del modello IFC rispondono in modo esatto a conteggi e superfici: non serve alcun modello d’immagine.',
  },
  'The rooms of this PDF were read from its vector lines and printed text, so counts and areas come from that index without an image model.': {
    de: 'Die Räume dieses PDFs wurden aus seinen Vektorlinien und dem gedruckten Text gelesen; Zählungen und Flächen stammen aus diesem Index, ohne Bildmodell.',
    fr: 'Les pièces de ce PDF ont été lues dans ses lignes vectorielles et son texte imprimé ; comptages et surfaces viennent de cet index, sans modèle d’image.',
    fi: 'Tämän PDF:n huoneet luettiin sen vektoriviivoista ja painetusta tekstistä, joten lukumäärät ja pinta-alat tulevat siitä ilman kuvamallia.',
    it: 'I locali di questo PDF sono stati letti dalle linee vettoriali e dal testo stampato: conteggi e superfici vengono da questo indice, senza modello d’immagine.',
  },
  'These values were read by OCR from the page image, so the answer is an estimate.': {
    de: 'Diese Werte wurden per OCR aus dem Seitenbild gelesen; die Antwort ist daher eine Schätzung.',
    fr: 'Ces valeurs ont été lues par OCR sur l’image de la page ; la réponse est donc une estimation.',
    fi: 'Nämä arvot luettiin tekstintunnistuksella sivun kuvasta, joten vastaus on arvio.',
    it: 'Questi valori sono stati letti con l’OCR dall’immagine della pagina: la risposta è quindi una stima.',
  },
  'No model has checked this answer.': {
    de: 'Kein Modell hat diese Antwort geprüft.', fr: 'Aucun modèle n’a vérifié cette réponse.',
    fi: 'Mikään malli ei ole tarkistanut tätä vastausta.', it: 'Nessun modello ha verificato questa risposta.',
  },
  'Abstained. Nothing guessed.': {
    de: 'Keine Antwort. Nichts geraten.', fr: 'Abstention. Rien n’a été deviné.',
    fi: 'Ei vastausta. Mitään ei arvattu.', it: 'Nessuna risposta. Nulla è stato indovinato.',
  },
}

/** Things counted, as the backend names them in English (plural), per language: [one, many]. */
const NOUNS: Record<string, Partial<Record<Lang, [string, string]>>> = {
  door: { de: ['Tür', 'Türen'], fr: ['porte', 'portes'], fi: ['ovi', 'ovea'], it: ['porta', 'porte'] },
  window: { de: ['Fenster', 'Fenster'], fr: ['fenêtre', 'fenêtres'], fi: ['ikkuna', 'ikkunaa'], it: ['finestra', 'finestre'] },
  room: { de: ['Raum', 'Räume'], fr: ['pièce', 'pièces'], fi: ['huone', 'huonetta'], it: ['locale', 'locali'] },
  toilet: { de: ['WC', 'WCs'], fr: ['WC', 'WC'], fi: ['WC', 'WC:tä'], it: ['WC', 'WC'] },
  washbasin: { de: ['Waschbecken', 'Waschbecken'], fr: ['lavabo', 'lavabos'], fi: ['pesuallas', 'pesuallasta'], it: ['lavabo', 'lavabi'] },
  sink: { de: ['Spüle', 'Spülen'], fr: ['évier', 'éviers'], fi: ['tiskiallas', 'tiskiallasta'], it: ['lavello', 'lavelli'] },
  bathtub: { de: ['Badewanne', 'Badewannen'], fr: ['baignoire', 'baignoires'], fi: ['kylpyamme', 'kylpyammetta'], it: ['vasca', 'vasche'] },
  shower: { de: ['Dusche', 'Duschen'], fr: ['douche', 'douches'], fi: ['suihku', 'suihkua'], it: ['doccia', 'docce'] },
}
const singular = (w: string) => (w.endsWith('s') ? w.slice(0, -1) : w)
const noun = (word: string, n: number, lang: Lang): string | null => {
  const pair = NOUNS[singular(word.toLowerCase())]?.[lang]
  return pair ? pair[n === 1 ? 0 : 1] : null
}

/** Step sentences with numbers in them: [pattern, sentence per language]. */
const PATTERNS: [RegExp, (m: RegExpMatchArray, lang: Lang) => string | null][] = [
  [/^(\d+) ([a-z]+)\. Each one is drawn on the plan for you to check\.$/, (m, lang) => {
    const w = noun(m[2], Number(m[1]), lang)
    if (!w) return null
    return {
      de: `${m[1]} ${w}. Jedes Element ist zur Kontrolle im Plan eingezeichnet.`,
      fr: `${m[1]} ${w}. Chacune est tracée sur le plan pour vérification.`,
      fi: `${m[1]} ${w}. Jokainen on piirretty pohjaan tarkistettavaksi.`,
      it: `${m[1]} ${w}. Ognuno è segnato sulla pianta per il controllo.`,
    }[lang as 'de' | 'fr' | 'fi' | 'it'] ?? null
  }],
  [/^About (\d+) ([a-z]+)\. Model estimate from (\d+) boxes found, check the boxes\.$/, (m, lang) => {
    const w = noun(m[2], Number(m[1]), lang)
    if (!w) return null
    return {
      de: `Etwa ${m[1]} ${w}. Modellschätzung aus ${m[3]} gefundenen Rahmen; bitte die Rahmen prüfen.`,
      fr: `Environ ${m[1]} ${w}. Estimation du modèle à partir de ${m[3]} cadres trouvés ; vérifiez les cadres.`,
      fi: `Noin ${m[1]} ${w}. Mallin arvio ${m[3]} löydetystä kehyksestä; tarkista kehykset.`,
      it: `Circa ${m[1]} ${w}. Stima del modello da ${m[3]} riquadri trovati; controllare i riquadri.`,
    }[lang as 'de' | 'fr' | 'fi' | 'it'] ?? null
  }],
  [/^Sum of (\d+) net room areas = ([\d.,]+) m², shown room by room\.$/, (m, lang) => ({
    de: `Summe von ${m[1]} Nettoraumflächen = ${m[2]} m², Raum für Raum gezeigt.`,
    fr: `Somme de ${m[1]} surfaces nettes de pièces = ${m[2]} m², montrée pièce par pièce.`,
    fi: `${m[1]} huoneen nettoalojen summa = ${m[2]} m², näytetty huoneittain.`,
    it: `Somma di ${m[1]} superfici nette dei locali = ${m[2]} m², mostrata locale per locale.`,
  }[lang as 'de' | 'fr' | 'fi' | 'it'] ?? null)],
  [/^([\d.,]+) m², shown on the plan\.$/, (m, lang) => ({
    de: `${m[1]} m², im Plan gezeigt.`, fr: `${m[1]} m², montré sur le plan.`,
    fi: `${m[1]} m², näytetty pohjassa.`, it: `${m[1]} m², mostrato sulla pianta.`,
  }[lang as 'de' | 'fr' | 'fi' | 'it'] ?? null)],
]

/**
 * The sentence of a reasoning-tree step in the interface language where it is one of the fixed sentences (or a
 * fixed sentence around numbers); anything else (a tool's own summary, a model's words) stays as sent.
 */
export function traceBody(body: string, lang: Lang): string {
  if (lang === 'en' || !body) return body
  const fixed = BODIES[body]?.[lang]
  if (fixed) return fixed
  for (const [re, say] of PATTERNS) {
    const m = body.match(re)
    if (m) return say(m, lang) ?? body
  }
  return body
}
