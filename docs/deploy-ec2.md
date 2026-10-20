# Deploy on AWS EC2 (with capacity tests)

This guide puts StageVote on one EC2 server with HTTPS, then runs the same three capacity
tests that were run during development, on the server itself. Plan about one hour the first time.

You need: an AWS account, a domain you control (for example `vote.techsarasolutions.com`), and the
Gmail App Password used for sending invitations ([email-setup.md](email-setup.md)).

## 1. Create the server

In the AWS console, region **Asia Pacific (Mumbai) ap-south-1**, *EC2 › Launch instance*:

| Setting | Value |
|---|---|
| Name | `dhanvarsha-vote` |
| Image | **Ubuntu Server 24.04 LTS** or **Amazon Linux 2023**, 64-bit (x86) |
| Instance type | **c5a.4xlarge** (16 vCPU, 32 GiB). Smaller also works: t3.large for rehearsals |
| Key pair | create one, download the `.pem` file and keep it safe |
| Storage | **40 GiB gp3** |
| Security group | allow **SSH (22) from My IP**, **HTTP (80)** and **HTTPS (443)** from anywhere. Nothing else |

Then *Elastic IPs › Allocate* and *Associate* it with the instance, so the address never changes.

## 2. Point your domain at it

At your DNS provider, add an **A record**: name `vote` (or the full host name), value = the
Elastic IP. On Cloudflare, set the record to **DNS only** (grey cloud): the server gets its own
HTTPS certificate. Wait until `ping vote.techsarasolutions.com` shows the Elastic IP.

## 3. Install Docker

The login user is `ubuntu` on Ubuntu and `ec2-user` on Amazon Linux (used below).

```sh
ssh -i Dhanvarsha.pem ec2-user@<elastic-ip>
sudo dnf install -y git                # Amazon Linux only; Ubuntu has git
git clone https://github.com/namanjain221995/Dhanvarsha.git
cd Dhanvarsha
sudo scripts/server-setup.sh
exit                                   # log out and back in so docker works without sudo
```

## 4. Configure

```sh
ssh -i Dhanvarsha.pem ec2-user@<elastic-ip>
cd Dhanvarsha
scripts/deploy.sh --domain vote.techsarasolutions.com --acme-email naman.jain@techsarasolutions.com
```

This creates `.env` with new random secrets and writes the production settings for the domain
(including `API_WORKERS`, half the CPU cores). It then lists what is still missing. Open the file
with `nano .env` and set the email lines (the same values that work on the development machine):

```dotenv
EMAIL_PROVIDER=smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_TLS_MODE=starttls
SMTP_USERNAME=naman.jain@techsarasolutions.com
SMTP_PASSWORD=<16-letter Google App Password, no spaces>
MAIL_FROM="Dhanvarsha <naman.jain@techsarasolutions.com>"
ADMIN_BOOTSTRAP_EMAIL=<your email: the first organiser account>
```

`ADMIN_BOOTSTRAP_PASSWORD` already holds a generated password: see it with
`grep ADMIN_BOOTSTRAP_PASSWORD .env`. Save with Ctrl+O, Enter, Ctrl+X. Then:

```sh
scripts/deploy.sh --check              # checks .env and DNS; changes nothing
```

## 5. Deploy and test

```sh
scripts/deploy.sh --load-test
```

This builds and starts the site, waits until `https://<domain>` answers, checks the database
permissions, and then runs the three capacity tests, one after another (about 15–20 minutes
on c5a.4xlarge):

| Test | Users | Requests | Pause per user |
|---|---|---|---|
| 1 | 500 | 10,000 | 1 second |
| 2 | 500 | 100,000 | none (stress) |
| 3 | 500 | 1,000,000 | none (endurance) |

The tests run against a separate test copy on the same server, with 500 synthetic accounts and
its own database and test inbox. **The live site, its database and its email are not touched.**
The tests use most of the CPU while they run, so run them before the event and **never during
it**. Use `--quick-load-test` to skip the million-request test. To deploy without tests, run
`scripts/deploy.sh`.

At the end the script prints a summary, and the passed reports are in `reports-test/` on the
server.

## 6. Look at the reports

Either download them to your computer:

```sh
scp -i Dhanvarsha.pem -r ec2-user@<elastic-ip>:Dhanvarsha/reports-test .
```

and open `reports-test/index.html`. Or view them through an SSH tunnel:

```sh
ssh -i Dhanvarsha.pem -L 8100:localhost:8100 ec2-user@<elastic-ip> \
  'cd Dhanvarsha && python3 -m http.server 8100 --bind 127.0.0.1 --directory reports-test'
```

then open http://localhost:8100/ on your computer (Ctrl+C closes it).

Compare them with the development results in [verification.md](verification.md): the pass rule
is fewer than 1% failed requests, 95% of answers within 1 second, and every accepted vote stored.

## 7. Set up the event

1. Open `https://<domain>/admin/login` and sign in with `ADMIN_BOOTSTRAP_EMAIL` and the
   generated password. Then delete the password value from `.env` (`nano .env`).
2. *Programme settings*: name **Dhanvarsha**, description, logo, sign-in opening and closing time.
3. *Email*: run diagnostics, then send yourself an invitation.
4. *Events*: add the real rounds, teams and team members.
5. *Participants › Add people*: paste the voters' email addresses and send the invitations.

The server starts with an empty database: nothing from the development machine (demo rounds,
fake participants) is copied. That is deliberate.

## Automatic deploys (CI/CD)

`.github/workflows/ci-cd.yml` runs on every push and pull request: backend lint, type checks
and the integration tests on a real PostgreSQL, and the web type checks and lint. When a push to
`main` passes, it deploys to the server. Connect the server once, from your computer (needs the
`gh` CLI signed in to GitHub):

```sh
scripts/setup-cicd.sh --host <elastic-ip> --user ec2-user --key Dhanvarsha.pem
```

It creates a new SSH key just for GitHub Actions, allows it on the server with a forced
command (that key can only fetch `main` and run `scripts/deploy.sh`: no shell), and stores the
four `DEPLOY_*` secrets in the repository. Your `.pem` key stays on your computer. Until this is
done, the pipeline runs the tests and skips the deploy.

A deploy refuses to restart the site while a voting round is open, so a push during the event
waits safely: the run fails with "Not deploying: voting is open", and you run it again from
GitHub › Actions › CI/CD › *Run workflow* after the round closes. Do not push changes during the
event unless they fix a problem.

## Without a domain yet

`scripts/deploy.sh --domain <ip-with-dashes>.sslip.io` (for example `52-91-241-136.sslip.io`)
gives the server a working name and a real HTTPS certificate straight away: sslip.io answers
any name that contains an IP address with that address. Some company networks block sslip.io
(for example a Fortinet web filter shows its own "blocked" certificate); phones on mobile data
work. For the event, use your own domain:

1. Attach an **Elastic IP** (otherwise the address changes when the instance stops).
2. Add the DNS A record `vote.techsarasolutions.com` → that IP (Cloudflare: *DNS only*).
3. On the server: `scripts/deploy.sh --domain vote.techsarasolutions.com --acme-email <you>`,
   then `scripts/deploy.sh`. Invitations sent before the change contain the old link: send
   them again (or send them only after the switch).
4. If the IP changed, run `scripts/setup-cicd.sh` again with the new `--host`.

## Updates, backups and cost

- **Update** to the newest code: push to `main` (the pipeline deploys), or on the server
  `cd Dhanvarsha && git pull && scripts/deploy.sh`. Data stays.
- **Back up** before the event and after it: `scripts/backup.sh` (files in `backups/`), and an
  EBS snapshot in the AWS console.
- **Cost**: c5a.4xlarge is about 0.62 USD per hour while running (about 15 USD per day). After
  the event, back up, then stop or terminate the instance and release the Elastic IP.
