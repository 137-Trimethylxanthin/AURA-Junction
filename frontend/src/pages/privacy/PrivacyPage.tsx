import { useTitle } from "@/hooks/use-title"
import { PageFrame } from "@/pages/landing/chrome"
import { ISSUES_URL } from "@/pages/landing/links"
import { ProsePage, ProseSection } from "@/pages/landing/Prose"

export default function PrivacyPage() {
  useTitle("Privacy")
  return (
    <PageFrame>
      <ProsePage
        title="Privacy"
        updated="14 Nov 2026"
        lead="AURA has no accounts, no cookies and no trackers. This page says what happens to a drawing you upload and to the questions you ask."
      >
        <ProseSection title="No accounts, no cookies, no tracking">
          <p>
            You do not sign up or log in. Neither the site nor our server sets
            cookies, and the site loads no analytics or advertising scripts.
          </p>
          <p>
            Your browser keeps a few things in its own local storage: the
            delete token of each drawing you upload, the id of your current
            conversation on each drawing (so a reload can bring it back), the
            presenter token if you opened a presenter link, and small interface
            choices. Each item goes only to our server, and only where it is
            needed: the conversation id with your next question, a delete
            token when you delete that drawing, the presenter token with an
            upload. Clearing this site’s data removes all of it.
          </p>
        </ProseSection>

        <ProseSection title="Drawings you upload">
          <ul className="flex list-disc flex-col gap-1.5 pl-5">
            <li>The file is processed on our server, which runs on Modal.</li>
            <li>
              An hourly clean-up deletes it 24 hours after upload. Deleting it in
              the app with its delete token removes it from the server at once
              and stops it being served.
            </li>
            <li>
              The DXF file itself is never sent to a model. Counts and areas are
              computed by code on our server. When a question needs a model (see
              below), text taken from your drawing goes with it: room names and
              numbers, apartment letters, room areas and printed text.
            </li>
            <li>
              DXF, IFC and PDF uploads are open; image-only uploads (PNG, JPG,
              WebP) use a presenter link. Routine CAD and IFC takeoffs are
              computed by code on our server. PDFs, images, and open-ended
              questions our deterministic router cannot solve are analysed inside
              an isolated container sandbox using the Google Gemini API, with
              every model call and cost shown.
            </li>
          </ul>
        </ProseSection>

        <ProseSection title="Questions and answers">
          <ul className="flex list-disc flex-col gap-1.5 pl-5">
            <li>
              Questions our own routing understands are answered without any
              model and never leave our server.
            </li>
            <li>
              Only when our routing cannot match a question is it sent to the
              Google Gemini API. The request holds the question, the room labels
              and apartment letters of the drawing, and, when the model looks
              things up, the results it asked for (room names and areas, counts,
              printed text) and up to the last six questions and answers of the
              conversation. For library drawings a low-resolution copy of the
              public dataset image may go with it; for an image upload, that
              image. Every such call is shown on the answer with its cost.
            </li>
            <li>
              The “tidy this up” button sends the text you typed and the
              drawing’s room labels to Gemini, as one call shown with its cost.
            </li>
            <li>
              Stored answers and conversations, including the question text,
              are deleted after 24 hours by the same clean-up, and so are the
              copies the server keeps in memory.
            </li>
          </ul>
        </ProseSection>

        <ProseSection title="Answer checks on sample drawings">
          <p>
            To check an answer on a drawing from our library, zoomed tiles cut
            from that drawing’s public dataset image are sent to the Gemini API
            once per drawing and item type, and the result is cached for the
            event. These images come from public datasets, not from you.
          </p>
          <p>
            Google processes these requests under the{" "}
            <a href="https://ai.google.dev/gemini-api/terms">
              Gemini API terms
            </a>
            .
          </p>
        </ProseSection>

        <ProseSection title="Request logs">
          <p>
            Our server uses your IP address to apply rate limits and does not
            store it. Where it is needed, a hash of it made with a secret salt is
            used instead: in the server’s log output (time, path or model,
            status, latency), and in the record of model requests that the
            hourly rate limits count, where the hash is removed after 24
            hours. Upload counts per address are kept in memory only. The record
            of model requests itself (time, model, purpose, tokens, cost) is
            kept as our budget counter. None of this holds question text or
            browser details.
          </p>
          <p>
            Our hosting providers (Modal for the server, Cloudflare for this
            site) keep their own request logs under their own retention rules,
            and those can include IP addresses. Our own records stay on the
            server’s storage until we delete them by hand after the hackathon;
            stopping the server does not delete them.
          </p>
        </ProseSection>

        <ProseSection title="Contact">
          <p>
            For questions or a deletion request, open an issue in the{" "}
            <a href={ISSUES_URL}>project repository</a>. Do not post a delete
            token or anything private in a public issue.
          </p>
        </ProseSection>
      </ProsePage>
    </PageFrame>
  )
}
