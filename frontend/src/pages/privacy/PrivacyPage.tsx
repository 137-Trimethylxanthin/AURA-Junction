import { PageFrame, useTitle } from "@/pages/landing/chrome"
import { ISSUES_URL } from "@/pages/landing/links"
import { ProsePage, ProseSection } from "@/pages/landing/Prose"

export default function PrivacyPage() {
  useTitle("Privacy · AURA")
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
            Your browser keeps the delete token of each drawing you upload in
            its own local storage. Clearing this site’s data removes it.
          </p>
        </ProseSection>

        <ProseSection title="Drawings you upload">
          <ul className="flex list-disc flex-col gap-1.5 pl-5">
            <li>The file is processed on our server, which runs on Modal.</li>
            <li>
              It is deleted 24 hours after upload by an hourly clean-up, or at
              once when you delete it with its delete token in the app.
            </li>
            <li>
              DXF files are answered by code on our server. Nothing from them is
              sent to Google.
            </li>
            <li>
              Image and PDF uploads are open only with a presenter link. Those
              images are sent to the Google Gemini API to detect and check
              items.
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
              Only when our routing cannot match a question is its text sent to
              the Google Gemini API to work out what you asked.
            </li>
            <li>
              Answers and conversations, including the question text, are
              deleted after 24 hours by the same clean-up.
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
            Your IP address is used in memory to apply rate limits and is never
            stored. Logs keep a salted hash of it instead, with the time, the
            path or model called, the status, the latency, and for model calls
            the token count and cost. They hold no question text, no browser
            details and no raw IP address.
          </p>
          <p>
            Logs are kept for the duration of the hackathon and deleted when the
            server is taken down.
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
