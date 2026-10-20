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
| Image | **Ubuntu Server 24.04 LTS**, 64-bit (x86) |
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

```sh
ssh -i dhanvarsha.pem ubuntu@<elastic-ip>
git clone https://github.com/namanjain221995/Dhanvarsha.git
cd Dhanvarsha
sudo scripts/server-setup.sh
exit                                   # log out and back in so docker works without sudo
```

## 4. Configure

```sh
ssh -i dhanvarsha.pem ubuntu@<elastic-ip>
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
scp -i dhanvarsha.pem -r ubuntu@<elastic-ip>:Dhanvarsha/reports-test .
```

and open `reports-test/index.html`. Or view them through an SSH tunnel:

```sh
ssh -i dhanvarsha.pem -L 8100:localhost:8100 ubuntu@<elastic-ip> \
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

## Updates, backups and cost

- **Update** to the newest code: `cd Dhanvarsha && git pull && scripts/deploy.sh`. Data stays.
- **Back up** before the event and after it: `scripts/backup.sh` (files in `backups/`), and an
  EBS snapshot in the AWS console.
- **Cost**: c5a.4xlarge is about 0.62 USD per hour while running (about 15 USD per day). After
  the event, back up, then stop or terminate the instance and release the Elastic IP.
