import { PageFrame, useTitle } from "@/pages/landing/chrome"
import { LICENSE_URL, THIRD_PARTY_URL } from "@/pages/landing/links"
import { ProsePage, ProseSection, Rows } from "@/pages/landing/Prose"

export default function CreditsPage() {
  useTitle("Credits and licences · AURA")
  return (
    <PageFrame>
      <ProsePage
        title="Credits and licences"
        updated="14 Nov 2026"
        lead="AURA is built on open datasets and open-source software. This page lists what we use, under which licence, and what we changed."
      >
        <ProseSection title="Swiss Dwellings: the sample drawing">
          <p>
            The sample floor plan on this site and in the app is rendered from
            the Swiss Dwellings dataset. We redraw it from the dataset geometry;
            the room labels and the numbering are synthetic.
          </p>
          <Rows
            rows={[
              ["Dataset", "Swiss Dwellings v3.0.0"],
              ["Authors", "Matthias Standfest et al., Archilyse AG"],
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
                "Drawing used",
                "site 11420, building 17737, floor 46639 (DXF geometry and PNG)",
              ],
              [
                "Changes",
                "Rendered from the dataset geometry; labels and numbering are synthetic.",
              ],
            ]}
          />
        </ProseSection>

        <ProseSection title="CubiCasa5k: evaluation only">
          <p>
            We use CubiCasa5k on our own machines to measure how well AURA reads
            raster floor plans. Its images are not published on this site, not
            shipped in the repository, and no model trained on it is
            distributed.
          </p>
          <Rows
            rows={[
              [
                "Dataset",
                <a href="https://github.com/CubiCasa/CubiCasa5k">CubiCasa5k</a>,
              ],
              [
                "Licence",
                <a href="https://creativecommons.org/licenses/by-nc-sa/4.0/">
                  CC BY-NC-SA 4.0
                </a>,
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
            Built by a team at the Junction 2026 hackathon for the Stoa
            challenge “Make Blueprints Talk”. AURA is not affiliated with or
            endorsed by Stoa. The code is open source under the{" "}
            <a href={LICENSE_URL}>MIT licence</a>.
          </p>
        </ProseSection>
      </ProsePage>
    </PageFrame>
  )
}
