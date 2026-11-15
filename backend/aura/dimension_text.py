"""The sentences of a length answer (dimension_answers.py) in the question's language.

`tx(lang, key, **vars)` fills one sentence; English is the fallback for a language or key that is missing. Quoted
values (what the sheet prints, room labels) are never translated. German uses Swiss spelling (ss), like the interface.
"""

from __future__ import annotations

LANGS = ("en", "de", "fr", "fi", "it")

TEXT: dict[str, dict[str, str]] = {
    "printed_as": {
        "en": "{t} as printed = {m}", "de": "{t} wie gedruckt = {m}", "fr": "{t} tel qu’imprimé = {m}",
        "fi": "{t} kuten painettu = {m}", "it": "{t} come stampato = {m}"},
    "printed_unit": {
        "en": "{t} {unit} as printed = {m}", "de": "{t} {unit} wie gedruckt = {m}",
        "fr": "{t} {unit} tel qu’imprimé = {m}", "fi": "{t} {unit} kuten painettu = {m}",
        "it": "{t} {unit} come stampato = {m}"},
    "written_units": {
        "en": "{m} (written {t} in the drawing's own units)",
        "de": "{m} (als {t} in den Einheiten der Zeichnung geschrieben)",
        "fr": "{m} (écrit {t} dans les unités propres au dessin)",
        "fi": "{m} (kirjoitettu {t} piirustuksen omissa yksiköissä)",
        "it": "{m} (scritto {t} nelle unità proprie del disegno)"},
    "m_as_printed": {
        "en": "{m} as printed", "de": "{m} wie gedruckt", "fr": "{m} tel qu’imprimé", "fi": "{m} kuten painettu",
        "it": "{m} come stampato"},
    "side_overall": {
        "en": "{name}: {p} (the overall dimension printed on the sheet)",
        "de": "{name}: {p} (das auf dem Blatt gedruckte Gesamtmass)",
        "fr": "{name} : {p} (la cote hors tout imprimée sur la feuille)",
        "fi": "{name}: {p} (arkille painettu kokonaismitta)",
        "it": "{name}: {p} (la quota complessiva stampata sulla tavola)"},
    "side_chain": {
        "en": "{name}: {m}, the sum of {n} dimensions printed in a row along one side ({parts}); no overall dimension "
              "is printed",
        "de": "{name}: {m}, die Summe von {n} Massen, die in einer Reihe entlang einer Seite gedruckt sind ({parts}); "
              "ein Gesamtmass ist nicht gedruckt",
        "fr": "{name} : {m}, la somme de {n} cotes imprimées en ligne le long d’un côté ({parts}) ; aucune cote hors "
              "tout n’est imprimée",
        "fi": "{name}: {m}, {n} peräkkäin yhdelle sivulle painetun mitan summa ({parts}); kokonaismittaa ei ole "
              "painettu",
        "it": "{name}: {m}, la somma di {n} quote stampate in fila lungo un lato ({parts}); nessuna quota complessiva "
              "è stampata"},
    "side_longest": {
        "en": "{name}: {p}, the longest {o} dimension printed; no overall dimension is printed, so the whole side may "
              "be longer",
        "de": "{name}: {p}, das längste gedruckte Mass ({o}); ein Gesamtmass ist nicht gedruckt, die ganze Seite kann "
              "also länger sein",
        "fr": "{name} : {p}, la plus longue cote imprimée ({o}) ; aucune cote hors tout n’est imprimée, le côté "
              "entier peut donc être plus long",
        "fi": "{name}: {p}, pisin painettu mitta ({o}); kokonaismittaa ei ole painettu, joten koko sivu voi olla "
              "pidempi",
        "it": "{name}: {p}, la quota stampata più lunga ({o}); nessuna quota complessiva è stampata, quindi l’intero "
              "lato può essere più lungo"},
    "o_horizontal": {"en": "horizontal", "de": "horizontal", "fr": "horizontale", "fi": "vaakasuuntainen",
                     "it": "orizzontale"},
    "o_vertical": {"en": "vertical", "de": "vertikal", "fr": "verticale", "fi": "pystysuuntainen", "it": "verticale"},
    "name_width": {
        "en": "Width (across the page)", "de": "Breite (quer über das Blatt)", "fr": "Largeur (en travers de la page)",
        "fi": "Leveys (sivun poikki)", "it": "Larghezza (attraverso il foglio)"},
    "name_length": {
        "en": "Depth (up the page)", "de": "Tiefe (das Blatt hinauf)", "fr": "Profondeur (vers le haut de la page)",
        "fi": "Syvyys (sivua ylöspäin)", "it": "Profondità (lungo il foglio verso l’alto)"},
    "lab_est": {
        "en": "an estimate: the sheet states no scale", "de": "eine Schätzung: das Blatt nennt keinen Massstab",
        "fr": "une estimation : la feuille n’indique pas d’échelle",
        "fi": "arvio: arkissa ei ole mittakaavaa", "it": "una stima: la tavola non indica una scala"},
    "lab_scan": {
        "en": "measured on the outlines traced on the {on}, not printed: an estimate",
        "de": "an den Umrissen gemessen, die auf dem {on} nachgezeichnet wurden, nicht gedruckt: eine Schätzung",
        "fr": "mesuré sur les contours tracés {on}, non imprimé : une estimation",
        "fi": "mitattu ääriviivoista, jotka on piirretty lähteestä ({on}), ei painettu: arvio",
        "it": "misurato sui contorni tracciati {on}, non stampato: una stima"},
    "lab_plan": {
        "en": "measured on the plan, not printed", "de": "auf dem Plan gemessen, nicht gedruckt",
        "fr": "mesuré sur le plan, non imprimé", "fi": "mitattu pohjapiirroksesta, ei painettu",
        "it": "misurato sulla pianta, non stampato"},
    "lab_outline": {
        "en": "measured on the outline, not printed", "de": "am Umriss gemessen, nicht gedruckt",
        "fr": "mesuré sur le contour, non imprimé", "fi": "mitattu ääriviivasta, ei painettu",
        "it": "misurato sul contorno, non stampato"},
    "on_sheet_image": {"en": "sheet image", "de": "Blattbild", "fr": "sur l’image de la feuille", "fi": "arkin kuva",
                       "it": "sull’immagine della tavola"},
    "on_scan": {"en": "scan", "de": "Scan", "fr": "sur le scan", "fi": "skannaus", "it": "sulla scansione"},
    "lower_bound_width": {
        "en": "{name}: the sheet prints no overall dimension and no row of dimensions that spans this side; the "
              "longest {o} dimension printed is {p}, so the plan is at least that wide here",
        "de": "{name}: das Blatt druckt kein Gesamtmass und keine Massreihe über diese ganze Seite; das längste "
              "gedruckte Mass ({o}) ist {p}, der Plan ist hier also mindestens so breit",
        "fr": "{name} : la feuille n’imprime ni cote hors tout ni ligne de cotes couvrant ce côté ; la plus longue "
              "cote imprimée ({o}) est {p}, le plan est donc au moins aussi large ici",
        "fi": "{name}: arkille ei ole painettu kokonaismittaa eikä tämän sivun kattavaa mittariviä; pisin painettu "
              "mitta ({o}) on {p}, joten pohja on tässä vähintään sen levyinen",
        "it": "{name}: la tavola non stampa una quota complessiva né una fila di quote che copra questo lato; la "
              "quota stampata più lunga ({o}) è {p}, quindi qui la pianta è larga almeno tanto"},
    "lower_bound_length": {
        "en": "{name}: the sheet prints no overall dimension and no row of dimensions that spans this side; the "
              "longest {o} dimension printed is {p}, so the plan is at least that deep here",
        "de": "{name}: das Blatt druckt kein Gesamtmass und keine Massreihe über diese ganze Seite; das längste "
              "gedruckte Mass ({o}) ist {p}, der Plan ist hier also mindestens so tief",
        "fr": "{name} : la feuille n’imprime ni cote hors tout ni ligne de cotes couvrant ce côté ; la plus longue "
              "cote imprimée ({o}) est {p}, le plan est donc au moins aussi profond ici",
        "fi": "{name}: arkille ei ole painettu kokonaismittaa eikä tämän sivun kattavaa mittariviä; pisin painettu "
              "mitta ({o}) on {p}, joten pohja on tässä vähintään sen syvyinen",
        "it": "{name}: la tavola non stampa una quota complessiva né una fila di quote che copra questo lato; la "
              "quota stampata più lunga ({o}) è {p}, quindi qui la pianta è profonda almeno tanto"},
    "extent": {
        "en": "{name}: {approx}{m}, the extent of the {src} ({label})",
        "de": "{name}: {approx}{m}, die Ausdehnung ({src}; {label})",
        "fr": "{name} : {approx}{m}, l’étendue ({src} ; {label})",
        "fi": "{name}: {approx}{m}, ulottuma ({src}; {label})",
        "it": "{name}: {approx}{m}, l’estensione ({src}; {label})"},
    "src_rooms": {"en": "rooms", "de": "Räume", "fr": "pièces", "fi": "huoneet", "it": "locali"},
    "src_walls": {"en": "walls", "de": "Wände", "fr": "murs", "fi": "seinät", "it": "pareti"},
    "no_dim_side": {
        "en": "{name}: no dimension is printed along this side on the page shown",
        "de": "{name}: auf der gezeigten Seite ist entlang dieser Seite kein Mass gedruckt",
        "fr": "{name} : aucune cote n’est imprimée le long de ce côté sur la page affichée",
        "fi": "{name}: näytetyllä sivulla tälle sivulle ei ole painettu mittaa",
        "it": "{name}: nella pagina mostrata non è stampata alcuna quota lungo questo lato"},
    "why_overall": {
        "en": "The sheet prints no overall dimension, and this page has no walls or room outlines in a known scale "
              "to measure the plan's extent from.",
        "de": "Das Blatt druckt kein Gesamtmass, und diese Seite hat keine Wände oder Raumumrisse in einem bekannten "
              "Massstab, an denen sich die Ausdehnung des Plans messen liesse.",
        "fr": "La feuille n’imprime aucune cote hors tout, et cette page n’a ni murs ni contours de pièces à une "
              "échelle connue pour mesurer l’étendue du plan.",
        "fi": "Arkille ei ole painettu kokonaismittaa, eikä tällä sivulla ole seiniä tai huoneiden ääriviivoja "
              "tunnetussa mittakaavassa, joista pohjan ulottuman voisi mitata.",
        "it": "La tavola non stampa una quota complessiva e questa pagina non ha pareti né contorni dei locali in una "
              "scala nota da cui misurare l’estensione della pianta."},
    "other_page": {
        "en": " (Printed on another page of the file than the one shown.)",
        "de": " (Auf einer anderen Seite der Datei gedruckt als der gezeigten.)",
        "fr": " (Imprimé sur une autre page du fichier que celle affichée.)",
        "fi": " (Painettu tiedoston toiselle sivulle kuin näytetylle.)",
        "it": " (Stampato su una pagina del file diversa da quella mostrata.)"},
    "rule": {
        "en": "Width is the side across the page, depth the side up the page",
        "de": "Die Breite ist die Seite quer über das Blatt, die Tiefe die Seite das Blatt hinauf",
        "fr": "La largeur est le côté en travers de la page, la profondeur le côté vers le haut de la page",
        "fi": "Leveys on sivun poikki kulkeva sivu, syvyys sivua ylöspäin kulkeva sivu",
        "it": "La larghezza è il lato attraverso il foglio, la profondità il lato lungo il foglio verso l’alto"},
    "rule_turned": {
        "en": "; where a room's size is printed without a direction or the room is turned on the page, its shorter "
              "side is given as the width.",
        "de": "; wo die Grösse eines Raums ohne Richtung gedruckt ist oder der Raum auf dem Blatt gedreht liegt, gilt "
              "seine kürzere Seite als Breite.",
        "fr": " ; lorsque les dimensions d’une pièce sont imprimées sans direction ou que la pièce est tournée sur la "
              "page, son petit côté est donné comme largeur.",
        "fi": "; kun huoneen koko on painettu ilman suuntaa tai huone on sivulla käännettynä, sen lyhyempi sivu "
              "annetaan leveytenä.",
        "it": "; dove le misure di un locale sono stampate senza direzione o il locale è ruotato sul foglio, il lato "
              "più corto è dato come larghezza."},
    "why_room": {
        "en": "The sheet prints no size in this room, and the room has no outline on this page to measure (only its "
              "label was read)",
        "de": "Das Blatt druckt in diesem Raum keine Grösse, und der Raum hat auf dieser Seite keinen Umriss zum "
              "Messen (nur seine Beschriftung wurde gelesen)",
        "fr": "La feuille n’imprime aucune dimension dans cette pièce, et la pièce n’a pas de contour à mesurer sur "
              "cette page (seule son étiquette a été lue)",
        "fi": "Arkille ei ole painettu tämän huoneen kokoa, eikä huoneella ole tällä sivulla mitattavaa ääriviivaa "
              "(vain sen nimi luettiin)",
        "it": "La tavola non stampa misure in questo locale e il locale non ha un contorno da misurare in questa "
              "pagina (è stata letta solo la sua etichetta)"},
    "why_room_on": {
        "en": "; no dimension line printed beside the label is a plausible side of the room.",
        "de": "; keine neben der Beschriftung gedruckte Masslinie ist eine plausible Seite des Raums.",
        "fr": " ; aucune ligne de cote imprimée près de l’étiquette n’est un côté plausible de la pièce.",
        "fi": "; mikään nimen viereen painettu mittaviiva ei ole uskottava huoneen sivu.",
        "it": "; nessuna linea di quota stampata accanto all’etichetta è un lato plausibile del locale."},
    "why_room_off": {
        "en": "; its label is on another page of the file than the one shown, so the dimension lines beside it "
              "cannot be matched to it.",
        "de": "; seine Beschriftung steht auf einer anderen Seite der Datei als der gezeigten, die Masslinien daneben "
              "lassen sich ihm also nicht zuordnen.",
        "fr": " ; son étiquette se trouve sur une autre page du fichier que celle affichée, les lignes de cote "
              "voisines ne peuvent donc pas lui être associées.",
        "fi": "; sen nimi on tiedoston toisella sivulla kuin näytetyllä, joten viereisiä mittaviivoja ei voi "
              "yhdistää siihen.",
        "it": "; la sua etichetta è su una pagina del file diversa da quella mostrata, quindi le linee di quota "
              "accanto non possono esserle associate."},
    "room": {"en": "Room", "de": "Raum", "fr": "Pièce", "fi": "Huone", "it": "Locale"},
    "rank_max": {
        "en": "The largest room by area, {name} ({a} m²)", "de": "Der flächenmässig grösste Raum, {name} ({a} m²)",
        "fr": "La plus grande pièce en surface, {name} ({a} m²)", "fi": "Pinta-alaltaan suurin huone, {name} ({a} m²)",
        "it": "Il locale più grande per superficie, {name} ({a} m²)"},
    "rank_min": {
        "en": "The smallest room by area, {name} ({a} m²)", "de": "Der flächenmässig kleinste Raum, {name} ({a} m²)",
        "fr": "La plus petite pièce en surface, {name} ({a} m²)",
        "fi": "Pinta-alaltaan pienin huone, {name} ({a} m²)",
        "it": "Il locale più piccolo per superficie, {name} ({a} m²)"},
    "near_wide": {
        "en": "{p} wide (across the page)", "de": "{p} breit (quer über das Blatt)",
        "fr": "{p} de large (en travers de la page)", "fi": "{p} leveä (sivun poikki)",
        "it": "{p} di larghezza (attraverso il foglio)"},
    "near_deep": {
        "en": "{p} deep (up the page)", "de": "{p} tief (das Blatt hinauf)",
        "fr": "{p} de profondeur (vers le haut de la page)", "fi": "{p} syvä (sivua ylöspäin)",
        "it": "{p} di profondità (lungo il foglio verso l’alto)"},
    "near_none_width": {
        "en": "no dimension line for its width is printed beside the label",
        "de": "neben der Beschriftung ist keine Masslinie für seine Breite gedruckt",
        "fr": "aucune ligne de cote pour sa largeur n’est imprimée près de l’étiquette",
        "fi": "nimen viereen ei ole painettu mittaviivaa sen leveydelle",
        "it": "accanto all’etichetta non è stampata alcuna linea di quota per la sua larghezza"},
    "near_none_depth": {
        "en": "no dimension line for its depth is printed beside the label",
        "de": "neben der Beschriftung ist keine Masslinie für seine Tiefe gedruckt",
        "fr": "aucune ligne de cote pour sa profondeur n’est imprimée près de l’étiquette",
        "fi": "nimen viereen ei ole painettu mittaviivaa sen syvyydelle",
        "it": "accanto all’etichetta non è stampata alcuna linea di quota per la sua profondità"},
    "near_only_width": {
        "en": "no dimension line for its depth is printed beside the label; its width (across the page) is {p}",
        "de": "neben der Beschriftung ist keine Masslinie für seine Tiefe gedruckt; seine Breite (quer über das "
              "Blatt) ist {p}",
        "fr": "aucune ligne de cote pour sa profondeur n’est imprimée près de l’étiquette ; sa largeur (en travers "
              "de la page) est {p}",
        "fi": "nimen viereen ei ole painettu mittaviivaa sen syvyydelle; sen leveys (sivun poikki) on {p}",
        "it": "accanto all’etichetta non è stampata alcuna linea di quota per la sua profondità; la sua larghezza "
              "(attraverso il foglio) è {p}"},
    "near_only_depth": {
        "en": "no dimension line for its width is printed beside the label; its depth (up the page) is {p}",
        "de": "neben der Beschriftung ist keine Masslinie für seine Breite gedruckt; seine Tiefe (das Blatt hinauf) "
              "ist {p}",
        "fr": "aucune ligne de cote pour sa largeur n’est imprimée près de l’étiquette ; sa profondeur (vers le "
              "haut de la page) est {p}",
        "fi": "nimen viereen ei ole painettu mittaviivaa sen leveydelle; sen syvyys (sivua ylöspäin) on {p}",
        "it": "accanto all’etichetta non è stampata alcuna linea di quota per la sua larghezza; la sua profondità "
              "(lungo il foglio verso l’alto) è {p}"},
    "near_how": {
        "en": "read from the dimension lines printed beside the room's label",
        "de": "aus den Masslinien gelesen, die neben der Raumbeschriftung gedruckt sind",
        "fr": "lu sur les lignes de cote imprimées près de l’étiquette de la pièce",
        "fi": "luettu huoneen nimen viereen painetuista mittaviivoista",
        "it": "letto dalle linee di quota stampate accanto all’etichetta del locale"},
    "near_agrees": {
        "en": "; their product, {prod} m², agrees with the room's printed area ({a} m²)",
        "de": "; ihr Produkt, {prod} m², stimmt mit der gedruckten Fläche des Raums überein ({a} m²)",
        "fr": " ; leur produit, {prod} m², concorde avec la surface imprimée de la pièce ({a} m²)",
        "fi": "; niiden tulo, {prod} m², vastaa huoneen painettua pinta-alaa ({a} m²)",
        "it": "; il loro prodotto, {prod} m², concorda con la superficie stampata del locale ({a} m²)"},
    "near_loose": {
        "en": "; the room has no outline on this page, so a line may cover more or less than the room itself",
        "de": "; der Raum hat auf dieser Seite keinen Umriss, eine Linie kann also mehr oder weniger als den Raum "
              "selbst abdecken",
        "fr": " ; la pièce n’a pas de contour sur cette page, une ligne peut donc couvrir plus ou moins que la pièce "
              "elle-même",
        "fi": "; huoneella ei ole tällä sivulla ääriviivaa, joten viiva voi kattaa enemmän tai vähemmän kuin itse "
              "huoneen",
        "it": "; il locale non ha un contorno in questa pagina, quindi una linea può coprire più o meno del locale "
              "stesso"},
    "how_printed_t": {
        "en": "as printed ({t})", "de": "wie gedruckt ({t})", "fr": "tel qu’imprimé ({t})",
        "fi": "kuten painettu ({t})", "it": "come stampato ({t})"},
    "how_printed": {
        "en": "as printed", "de": "wie gedruckt", "fr": "tel qu’imprimé", "fi": "kuten painettu",
        "it": "come stampato"},
    "how_t_printed": {
        "en": "{t} as printed", "de": "{t} wie gedruckt", "fr": "{t} tel qu’imprimé", "fi": "{t} kuten painettu",
        "it": "{t} come stampato"},
    "how_est": {
        "en": "an estimate from the room outline: the sheet states no scale",
        "de": "eine Schätzung aus dem Raumumriss: das Blatt nennt keinen Massstab",
        "fr": "une estimation d’après le contour de la pièce : la feuille n’indique pas d’échelle",
        "fi": "arvio huoneen ääriviivasta: arkissa ei ole mittakaavaa",
        "it": "una stima dal contorno del locale: la tavola non indica una scala"},
    "how_scan": {
        "en": "measured on the outline traced on the {on}, not printed: an estimate",
        "de": "am Umriss gemessen, der auf dem {on} nachgezeichnet wurde, nicht gedruckt: eine Schätzung",
        "fr": "mesuré sur le contour tracé {on}, non imprimé : une estimation",
        "fi": "mitattu ääriviivasta, joka on piirretty lähteestä ({on}), ei painettu: arvio",
        "it": "misurato sul contorno tracciato {on}, non stampato: una stima"},
    "how_outline": {
        "en": "measured on the room outline, not printed", "de": "am Raumumriss gemessen, nicht gedruckt",
        "fr": "mesuré sur le contour de la pièce, non imprimé", "fi": "mitattu huoneen ääriviivasta, ei painettu",
        "it": "misurato sul contorno del locale, non stampato"},
    "line_w": {
        "en": "{name}: {approx}{m} wide (across the page; {how})",
        "de": "{name}: {approx}{m} breit (quer über das Blatt; {how})",
        "fr": "{name} : {approx}{m} de large (en travers de la page ; {how})",
        "fi": "{name}: {approx}{m} leveä (sivun poikki; {how})",
        "it": "{name}: {approx}{m} di larghezza (attraverso il foglio; {how})"},
    "line_d": {
        "en": "{name}: {approx}{m} deep (up the page; {how})",
        "de": "{name}: {approx}{m} tief (das Blatt hinauf; {how})",
        "fr": "{name} : {approx}{m} de profondeur (vers le haut de la page ; {how})",
        "fi": "{name}: {approx}{m} syvä (sivua ylöspäin; {how})",
        "it": "{name}: {approx}{m} di profondità (lungo il foglio verso l’alto; {how})"},
    "line_wd": {
        "en": "{name}: {approx}{w} wide (across the page) × {d} deep (up the page) ({how})",
        "de": "{name}: {approx}{w} breit (quer über das Blatt) × {d} tief (das Blatt hinauf) ({how})",
        "fr": "{name} : {approx}{w} de large (en travers de la page) × {d} de profondeur (vers le haut de la page) "
              "({how})",
        "fi": "{name}: {approx}{w} leveä (sivun poikki) × {d} syvä (sivua ylöspäin) ({how})",
        "it": "{name}: {approx}{w} di larghezza (attraverso il foglio) × {d} di profondità (lungo il foglio verso "
              "l’alto) ({how})"},
    "line_w_short": {
        "en": "{name}: {approx}{m} wide (the shorter side; {how})",
        "de": "{name}: {approx}{m} breit (die kürzere Seite; {how})",
        "fr": "{name} : {approx}{m} de large (le petit côté ; {how})",
        "fi": "{name}: {approx}{m} leveä (lyhyempi sivu; {how})",
        "it": "{name}: {approx}{m} di larghezza (il lato più corto; {how})"},
    "line_l_long": {
        "en": "{name}: {approx}{m} long (the longer side; {how})",
        "de": "{name}: {approx}{m} lang (die längere Seite; {how})",
        "fr": "{name} : {approx}{m} de long (le grand côté ; {how})",
        "fi": "{name}: {approx}{m} pitkä (pidempi sivu; {how})",
        "it": "{name}: {approx}{m} di lunghezza (il lato più lungo; {how})"},
    "ext_why": {
        "en": "No dimension line with a length is printed on this sheet (none was read)",
        "de": "Auf diesem Blatt ist keine Masslinie mit einer Länge gedruckt (es wurde keine gelesen)",
        "fr": "Aucune ligne de cote avec une longueur n’est imprimée sur cette feuille (aucune n’a été lue)",
        "fi": "Tälle arkille ei ole painettu mittaviivaa, jossa olisi pituus (yhtään ei luettu)",
        "it": "Su questa tavola non è stampata alcuna linea di quota con una lunghezza (nessuna è stata letta)"},
    "ext_areas": {
        "en": "; its {n} printed values are room areas", "de": "; seine {n} gedruckten Werte sind Raumflächen",
        "fr": " ; ses {n} valeurs imprimées sont des surfaces de pièces",
        "fi": "; sen {n} painettua arvoa ovat huoneiden pinta-aloja",
        "it": "; i suoi {n} valori stampati sono superfici di locali"},
    "ext_plan": {
        "en": " The plan itself measures {approx}{w} × {d} ({label}).",
        "de": " Der Plan selbst misst {approx}{w} × {d} ({label}).",
        "fr": " Le plan lui-même mesure {approx}{w} × {d} ({label}).",
        "fi": " Itse pohja on kooltaan {approx}{w} × {d} ({label}).",
        "it": " La pianta stessa misura {approx}{w} × {d} ({label})."},
    "ext_max": {
        "en": "The longest dimension printed on the sheet: {p} ({o}; of {n} printed dimensions).",
        "de": "Das längste auf dem Blatt gedruckte Mass: {p} ({o}; von {n} gedruckten Massen).",
        "fr": "La plus longue cote imprimée sur la feuille : {p} ({o} ; sur {n} cotes imprimées).",
        "fi": "Pisin arkille painettu mitta: {p} ({o}; {n} painetusta mitasta).",
        "it": "La quota più lunga stampata sulla tavola: {p} ({o}; su {n} quote stampate)."},
    "ext_min": {
        "en": "The shortest dimension printed on the sheet: {p} ({o}; of {n} printed dimensions).",
        "de": "Das kürzeste auf dem Blatt gedruckte Mass: {p} ({o}; von {n} gedruckten Massen).",
        "fr": "La plus courte cote imprimée sur la feuille : {p} ({o} ; sur {n} cotes imprimées).",
        "fi": "Lyhin arkille painettu mitta: {p} ({o}; {n} painetusta mitasta).",
        "it": "La quota più corta stampata sulla tavola: {p} ({o}; su {n} quote stampate)."},
}


def tx(lang: str, key: str, **kw: object) -> str:
    """One sentence in `lang` (English when the language is not translated), placeholders filled."""
    forms = TEXT[key]
    return forms.get(lang, forms["en"]).format(**kw)


def word(lang: str, prefix: str, value: object) -> str:
    """A translated word for a stored value ("horizontal", "rooms"), or the value itself when it has none."""
    forms = TEXT.get(f"{prefix}_{value}")
    return forms.get(lang, forms["en"]) if forms else str(value)
