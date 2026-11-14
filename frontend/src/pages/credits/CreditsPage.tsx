import { PageFrame, useTitle } from "@/pages/landing/chrome"
import { LICENSE_URL, THIRD_PARTY_URL } from "@/pages/landing/links"
import { ProsePage, ProseSection, Rows } from "@/pages/landing/Prose"

const KIT_URL = "https://www.ifcwiki.org/index.php?title=KIT_IFC_Examples"

export default function CreditsPage() {
  useTitle("Credits and licences · AURA")
  return (
    <PageFrame>
      <ProsePage
        title="Credits and licences"
        updated="14 Nov 2026"
        lead="AURA is built on open datasets and open-source software. This page lists what we use, under which licence, and what we changed."
      >
        <ProseSection title="Swiss Dwellings: sample and library drawings">
          <p>
            The sample floor plan on this site and the Swiss drawings in the app
            library are rendered by us from the geometry published in the Swiss
            Dwellings dataset. Archilyse AG does not endorse these drawings.
          </p>
          <Rows
            rows={[
              ["Dataset", "Swiss Dwellings v3.0.0"],
              [
                "Authors",
                "Matthias Standfest, Michael Franzen, Yvonne Schröder, Luis Gonzalez Medina, Yarilo Villanueva Hernandez, Jan Hendrik Buck, Yen-Ling Tan, Milena Niedzwiecka, Rachele Colmegna (Archilyse AG)",
              ],
              [
                "DOI",
                <a href="https://doi.org/10.5281/zenodo.7788422">
                  doi:10.5281/zenodo.7788422
                </a>,
              ],
              [
                "Licence",
                <a href="https://creativecommons.org/licenses/by/4.0/">
                  CC BY 4.0
                </a>,
              ],
              [
                "Sample drawing",
                "site 11420, building 17737, floor 46639 (DXF geometry and PNG)",
              ],
              [
                "Changes",
                "Rendered by us from the published geometry. We added the drawing style, room and unit numbers, room names in an assigned language, and a title block.",
              ],
            ]}
          />
        </ProseSection>

        <ProseSection title="IFC models in the library">
          <p>
            Four library drawings are 2D storey plans rendered from public IFC
            building models with IfcOpenShell. The model geometry is unmodified.
          </p>
          <Rows
            rows={[
              [
                "FZK-Haus",
                <>
                  Simple Phantasy Building. Institute for Automation and Applied
                  Informatics (IAI), Karlsruhe Institute of Technology (KIT),{" "}
                  <a href={KIT_URL}>KIT IFC Examples</a>. Licence: for
                  unrestricted use. Change: 2D plan rendered from the IFC with
                  IfcOpenShell, geometry unmodified.
                </>,
              ],
              [
                "Buerogebaeude",
                <>
                  Phantasy Office Building (AC20-Institute-Var-2). IAI, KIT,{" "}
                  <a href={KIT_URL}>KIT IFC Examples</a>. Licence: for
                  unrestricted use. Change: 2D plan rendered from the IFC with
                  IfcOpenShell, geometry unmodified.
                </>,
              ],
              [
                "Smiley West",
                <>
                  Terraced houses. IAI, KIT,{" "}
                  <a href={KIT_URL}>KIT IFC Examples</a>. Licence: for
                  unrestricted use. Change: 2D plan rendered from the IFC with
                  IfcOpenShell, geometry unmodified.
                </>,
              ],
              [
                "BIM-Speed Warmond",
                <>
                  Demo site, baseline. Garcia, Ana Galegos; Hartmann, Timo
                  (2022): BIM-Speed Demonstration Projects, BIM and BEM models.
                  TU Berlin DepositOnce,{" "}
                  <a href="https://doi.org/10.14279/depositonce-16441.2">
                    doi:10.14279/depositonce-16441.2
                  </a>{" "}
                  (EU H2020 BIM-SPEED, grant 820553). Licence:{" "}
                  <a href="https://creativecommons.org/licenses/by/4.0/">
                    CC BY 4.0
                  </a>
                  . Change: 2D plan rendered from the IFC with IfcOpenShell,
                  geometry unmodified.
                </>,
              ],
            ]}
          />
          <p className="text-sm text-muted-foreground">
            The Duplex and Medical-Dental Clinic models (published under CC BY
            4.0 today, originally CC BY-ND 3.0; we apply the stricter CC BY-ND
            3.0) are used only in accuracy counts, unchanged; no drawings are
            rendered from them.
          </p>
        </ProseSection>

        <ProseSection title="CubiCasa5k: evaluation only">
          <p>
            We use CubiCasa5k on our own machines to measure how well AURA reads
            raster floor plans. No CubiCasa images or annotations are in the
            repository or on this site, only aggregate accuracy numbers, and no
            model trained on it is distributed.
          </p>
          <Rows
            rows={[
              [
                "Dataset",
                <>
                  Kalervo, Ylioinas, Häikiö, Karhu, Kannala: “CubiCasa5K: A
                  Dataset and an Improved Multi-Task Model for Floorplan Image
                  Analysis”, SCIA 2019,{" "}
                  <a href="https://zenodo.org/records/2613548">
                    zenodo.org/records/2613548
                  </a>
                </>,
              ],
              ["Copyright", "© 2019 CubiCasa"],
              [
                "Licence",
                <>
                  <a href="https://creativecommons.org/licenses/by-nc-sa/4.0/">
                    CC BY-NC-SA 4.0
                  </a>{" "}
                  (the repository states CC BY-NC; we apply the stricter
                  licence)
                </>,
              ],
              ["Use", "Evaluation only, not redistributed"],
            ]}
          />
        </ProseSection>

        <ProseSection title="Open-source software">
          <p>
            The frontend uses React, Vite, Tailwind CSS, shadcn/ui with Radix
            UI, Motion, Zustand, React Router and Lucide icons. The backend uses
            FastAPI, ezdxf and Shapely. The full list with versions and licences
            is in <a href={THIRD_PARTY_URL}>THIRD_PARTY.md</a> in the
            repository.
          </p>
        </ProseSection>

        <ProseSection title="Fonts">
          <p>
            Inter by Rasmus Andersson and JetBrains Mono by JetBrains, both
            under the{" "}
            <a href="https://openfontlicense.org">SIL Open Font License 1.1</a>,
            served from this site through Fontsource.
          </p>
        </ProseSection>

        <ProseSection title="Models">
          <p>
            Answer checks and drawings without CAD data use the Google Gemini
            API. Geometry answers use no model at all.
          </p>
        </ProseSection>

        <ProseSection title="The team">
          <p>
            Built by KuK Hofclankerei at the Junction 2026 hackathon for the
            Stoa challenge “Make Blueprints Talk”. AURA is not affiliated with
            or endorsed by Stoa. The code is open source under the{" "}
            <a href={LICENSE_URL}>MIT licence</a>.
          </p>
        </ProseSection>
      </ProsePage>
    </PageFrame>
  )
}
