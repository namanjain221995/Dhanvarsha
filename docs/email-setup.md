# Email setup

StageVote sends two kinds of email, one recipient per message (never a shared To/CC list):

| Kind | Contains | Sent when |
|---|---|---|
| Invitation | the organisers' wording, a button carrying the participant's personal sign-in link, a fixed note under it (the button signs in on the first phone or computer it is opened on and again on that device whenever they come back, so the email should not be forwarded), the same link as text for when the button does not work, sign-in opening time and timezone, help contact, programme logo | an organiser invites or re-invites a participant |
| Invitation preview | the same design and wording with the example link `<APP_BASE_URL>/enter#token=example`, which never signs anyone in (the page says it comes from a preview email); the subject starts with `[Preview]` | only through the API (`POST /api/v1/admin/email/test`); the console sends real invitations instead |

The invitation is the sign-in. Its button opens `<APP_BASE_URL>/enter#token=<token>`, a
page whose only control is one button (*Enter the arena* on the gold look, *Sign in* on the
classic one), and tapping it signs the participant in. The token is random and personal,
and StageVote stores only its hash. The first phone or browser that taps the button keeps
the link: on that device the same email signs them in again whenever they come back, and on
any other device the page says the link is already in use. There is no password, no
activation step, no self-service sign-up and no self-service recovery. Re-sending an
invitation creates a new link (the old one stops working at once), lets it open on a new
device, and signs out any open session. Each message has a branded HTML part and a
plain-text part (`apps/api/src/stagevote/templates/email/`); the text part shows the link
after `Enter the arena: `. The programme logo is embedded in the message as a small PNG
(`cid:` image), so it shows even when the site is not reachable from the internet.

## Editing the invitation

*Admin › Email › Invitation email* edits the wording for the active programme: subject,
heading, message (above the button), closing note (below the button) and button text
(built-in: *Enter the arena*). The page shows the result for an example participant as you
type, with the same example link as preview emails. *Send invitations to* takes up to 50
addresses separated by commas: each person gets a real invitation with their own sign-in
link, people not on the participant list yet are added automatically, and anyone invited
before gets a new link (the old one stops working and they are signed out). One invalid
address sends nothing. Longer lists go through *Participants › Add people*. Placeholders:
`{name}` (the participant's name, or "there"), `{programme}`, `{opens_at}` and
`{help_contact}`; any other `{…}` is refused when saving. There is no placeholder for the
link. The sign-in button and its link, the note under it, the text copy of the link,
opening time, help contact and footer are always added by StageVote, so an edit cannot
remove them. Organiser text is escaped, never treated as HTML or as a template. Saving the
built-in wording removes the customisation. Invitations are rendered when they are sent, so
a saved change also applies to invitations still waiting in the queue. Wording saved
before sign-in links replaced passwords may still ask people to sign in with a password:
check it after upgrading, and edit it or restore the built-in wording.

Choose the provider with `EMAIL_PROVIDER`:

- `mailpit` – local development only (refused when `APP_ENV=production`).
- `smtp` – any SMTP submission service.
- `gmail_workspace` – Gmail API with a Google Workspace service account and domain-wide
  delegation.
- `disabled` – jobs stay queued until a provider is configured.

## What "accepted" means

The admin email page shows `queued`, `processing`, `accepted`, `failed`, `unknown`,
`superseded` and `cancelled`.

- **accepted**: the provider confirmed it took the message (SMTP `250` after `DATA`, or a
  Gmail API message id). It is **not** proof of inbox delivery. StageVote does not receive
  bounce or spam-folder information, so it never shows "delivered" or "bounced".
- **unknown**: the connection failed after the message body was handed over, or the worker
  stopped mid-send. The provider may or may not have accepted it. StageVote never retries
  these automatically; an organiser can retry after confirming that a duplicate is
  acceptable (the same link is resent, so a duplicate is harmless but confusing).
- **failed**: a permanent refusal (bad credentials, rejected recipient, missing
  delegation) or retries exhausted. Configuration errors pause all sending for 5 minutes
  so a wrong password does not burn through every queued job. Fix the configuration,
  then use *Retry all failed*.

Transient failures (connection refused, 4xx, rate limits, Gmail 429/5xx) are retried with
exponential backoff and jitter (about 30 s, 1 min, 2 min, … capped at 30 min) up to
`EMAIL_MAX_ATTEMPTS` (default 6). Each retry reuses the same `Message-ID` and the same
link, so a participant who receives two copies can use either. The encrypted copy of
the link needed for retries is kept for `INVITATION_RETRY_HOURS` (default 72) and
purged as soon as the provider accepts the message. After that StageVote keeps only a hash
of the token, so no one can look a link up again: a lost link is replaced by sending a new
invitation. Invitation jobs created before sign-in links, whose stored copy is a password,
are never sent; they fail as `retired_payload`.

## Local development: Mailpit

`compose.yaml` runs Mailpit. Every message from the local stack lands in the test inbox at
**http://localhost:8025** (SMTP on `mailpit:1025` inside Docker, `127.0.0.1:1025` on the host).
Nothing leaves the machine. Automated tests use Mailpit or an in-memory recording provider.

## Production option 1: SMTP

```dotenv
EMAIL_PROVIDER=smtp
MAIL_FROM="Innovation Night <voting@events.example.org>"
SMTP_HOST=smtp.your-provider.example
SMTP_PORT=587
SMTP_TLS_MODE=starttls      # or: tls with SMTP_PORT=465 (implicit TLS)
SMTP_USERNAME=<provider-username>
SMTP_PASSWORD=<provider-password-or-api-key>
EMAIL_MAX_PER_MINUTE=60
```

- `starttls` upgrades a plain connection (port 587); `tls` is implicit TLS (port 465).
  StageVote rejects mismatched combinations, and refuses `SMTP_TLS_MODE=none` or missing
  credentials in production. Certificates are verified.
- Use credentials your provider issues for SMTP submission. **A service-account private
  key is not an SMTP password**, and an ordinary Gmail account password does not work for
  SMTP.
- If you use Google Workspace over SMTP, Google's options are the SMTP relay service
  (configured by a Workspace administrator, `smtp-relay.gmail.com`) or an app password.
  App passwords exist only for accounts with 2-Step Verification, and are unavailable for
  accounts that use only security keys, accounts with Advanced Protection, and some
  work/school accounts managed by an organisation
  ([Google: app passwords](https://support.google.com/accounts/answer/185833)). The Gmail
  API option below avoids passwords altogether.
- Run *Admin › Email › Run diagnostics* (connects, upgrades TLS and authenticates without
  sending), then send an invitation to a mailbox you control. Only an accepted message
  proves the provider accepts mail from StageVote.
- **Never turn on click tracking** (also called link tracking) for StageVote's sender.
  Commercial sending services such as SendGrid, Mailgun, Amazon SES or Brevo can rewrite
  every link in a message so that clicks pass through their servers first. That rewrites
  each participant's personal link, keeps the whole link with its token in the provider's
  records, and can lose the `#token=…` part, which leaves the button signing nobody in.
  Check the setting in the provider's dashboard before sending invitations. Some company
  mail systems (for example Microsoft Defender Safe Links) rewrite links in incoming mail
  as well; StageVote cannot prevent that, but a scanner that only opens a link does not
  use it up, because only a tap on the page's button signs anyone in.

## Production option 2: Google Workspace Gmail API

Requirements: a Google Workspace domain with Gmail enabled, a real Workspace mailbox to send
from (for example `voting@yourdomain`), a Google Cloud project, and a Workspace **super
administrator** for the delegation step. A standalone service account cannot send as an
arbitrary personal Gmail user, and the service account's own `…iam.gserviceaccount.com`
address is never the participant-facing sender.

> **About the key that was shared during development.** A service-account private key was
> pasted into the build conversation. Treat it as compromised: in Google Cloud console ›
> IAM & Admin › Service accounts › *Keys*, **delete that key**, create a replacement only
> when you configure this integration, and never paste key material into chat, tickets or
> screenshots. StageVote's repository, images, logs and tests contain no part of it
> (no key material, no identifiers).

1. **Enable the Gmail API** in the Google Cloud project (APIs & Services › Library).
2. **Create or choose a service account** and create a JSON key (or use workload identity
   where your platform supports it). Note its **client ID** (numeric) on the service account
   page.
3. **Delegate domain-wide authority** (super administrator, Google Admin console):
   *Security › Access and data control › API controls › Manage domain wide delegation ›
   Add new*. Client ID: the service account's client ID. OAuth scopes:
   `https://www.googleapis.com/auth/gmail.send` (only this scope). Click *Authorize*.
   Google notes propagation can take up to 24 hours
   ([Google: service accounts and delegation](https://developers.google.com/identity/protocols/oauth2/service-account)).
4. **Mount the key read-only on the server**, never in an environment variable, image or
   web upload:
   ```sh
   mkdir -p secrets
   install -m 0400 /path/to/new-key.json secrets/google-service-account.json
   sudo chown 10001:10001 secrets/google-service-account.json   # the API/worker user in the image
   ```
   `compose.gmail.yaml` mounts it at `/run/secrets/google-service-account.json` for the API
   (diagnostics) and the worker (sending).
5. **Configure**:
   ```dotenv
   EMAIL_PROVIDER=gmail_workspace
   GOOGLE_WORKSPACE_SENDER=voting@yourdomain.example
   MAIL_FROM="Innovation Night <voting@yourdomain.example>"   # the sender or a verified send-as alias
   EMAIL_MAX_PER_MINUTE=30
   ```
   Start with `docker compose -f compose.prod.yaml -f compose.gmail.yaml up -d`.
6. **Diagnose, then test**: *Admin › Email › Run diagnostics* checks that the file is a
   service-account key, that the sender is a mailbox, that `MAIL_FROM` matches it, and that
   Google issues a delegated `gmail.send` token for the sender (an `unauthorized_client`
   error means step 3 is missing or not yet propagated). Finding the file is never reported
   as working delivery. Then send an invitation to yourself; only an accepted message proves
   sending works.

StageVote uses the official `google-auth` and `google-api-python-client` libraries, sends
RFC 5322 messages through `users.messages.send` with `userId=me` as the delegated sender
([Google: sending email](https://developers.google.com/workspace/gmail/api/guides/sending)).

### Testing the Gmail provider on the local stack

1. Put the service-account key in `secrets/google-service-account.json` (gitignored) and make
   it readable only by the container user: `docker run --rm -v "$PWD/secrets:/s" alpine:3.22
   sh -c 'chown 10001:10001 /s/google-service-account.json && chmod 0400 /s/google-service-account.json'`.
   You can still delete or replace the file later (the `secrets/` folder belongs to you).
2. In `.env`: `EMAIL_PROVIDER=gmail_workspace`,
   `GOOGLE_SERVICE_ACCOUNT_FILE=/run/secrets/google-service-account.json`,
   `GOOGLE_WORKSPACE_SENDER=<sender mailbox>`, `MAIL_FROM="StageVote <sender mailbox>"`, and
   `COMPOSE_FILE=compose.yaml:compose.gmail.yaml` so plain `docker compose` commands mount the key.
3. `docker compose up -d`, then *Admin › Email › Run diagnostics*, then send yourself an invitation.
4. To return to the local inbox: `EMAIL_PROVIDER=mailpit`, remove the `COMPOSE_FILE` line,
   `docker compose up -d`.

The browser and load test runners (`tests/e2e/run.sh`, `tests/load/run.sh`) always force their
isolated stacks onto their own Mailpit, so synthetic test addresses never reach a real provider.

## Sender reputation and DNS

Inbox placement is never guaranteed. Give it a fair chance:

- Send from your own domain. Publish **SPF** and sign with **DKIM** (Google Workspace:
  Admin console › Apps › Gmail › Authenticate email). Publish **DMARC** (start with `p=none`).
- Gmail's sender requirements: all senders need SPF *or* DKIM, valid forward and reverse
  DNS, TLS and a spam rate below 0.3%; senders of 5,000+ messages a day to Gmail need SPF
  *and* DKIM, DMARC and aligned From domains
  ([Google: email sender guidelines](https://support.google.com/a/answer/81126)).
  A 300–500 person programme is far below the bulk threshold.
- Send invitations a few days ahead, in one batch, so you can fix problems before the
  event. Ask participants to look in spam folders and to add the sender to contacts.

## Sending limits (check current values before the event)

| Limit | Value (Google documentation, October 2026) |
|---|---|
| Workspace messages per user per rolling 24 h | 2,000 (paid), 500 (trial) |
| Workspace external recipients per day | 3,000; unique external recipients 2,000 (500 on trial) |
| Gmail API per user per minute | 6,000 quota units; `messages.send` costs 100 units → at most about 60 sends per minute per sender |
| Gmail API recipients per message | 500 (StageVote sends one recipient per message) |

Sources: [Gmail sending limits in Google Workspace](https://knowledge.workspace.google.com/admin/gmail/gmail-sending-limits-in-google-workspace),
[Gmail API usage limits](https://developers.google.com/workspace/gmail/api/reference/quota).
Limits can change without notice, and reaching one blocks sending for up to 24 hours.

StageVote paces itself with `EMAIL_MAX_PER_MINUTE` (per worker) and `EMAIL_CONCURRENCY`.
For 300 invitations at 30 per minute the batch takes about 10 minutes; at 60 per minute about
5 minutes. Rate-limit responses (`429`, `rateLimitExceeded`) are retried with backoff.

The automated verification uses local Mailpit only (see [verification.md](verification.md)).
On 2026-10-20 the organiser configured Gmail SMTP (`smtp.gmail.com:587`, STARTTLS, a Google
App Password): diagnostics authenticated and a test message was accepted and arrived in the
organiser's Gmail inbox. In the event-day test on 2026-10-23, Gmail SMTP accepted about
120 invitations at 120 per minute and then refused the SMTP login with `454`. StageVote
logs in once per message, reports that refusal as `smtp_auth_failed`, marks the invitation
failed and pauses sending for 5 minutes. After a pause, about 210 more went through at 30
per minute before the next refusal. Plan a large batch, such as re-sending everyone a new
link, with this limit in mind
([deploy-ec2.md](deploy-ec2.md#switching-voters-from-passwords-to-sign-in-links)).
