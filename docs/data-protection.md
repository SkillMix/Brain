# Data protection: what this template does with personal data

A short orientation for whoever decides to run it, written from what the code does. It is not
legal advice. Read it with your counsel before you connect anyone's mail.

## What is copied, and where it goes

Each person's agent reads that person's own mail, chat, calls, calendar and documents through
the connectors their own Claude Code session has, with their own sign-ins. It copies what it
reads into git repositories your company owns on your Git host.

| What | Where | Committed to git? |
|---|---|---|
| Raw records as exported: full mail bodies with quoted history, full call transcripts with speaker names, chat threads, calendar descriptions, document and attachment text | `_meta/state/feed/` on the person's machine | no |
| Witness pages: one dated page per record, holding its full text | `knowledge/sources/` in the person's workspace | yes |
| Records the agent could not classify as work or personal | `_meta/state/private/unsure.jsonl` on the person's machine, listed by a one-line label in their own morning mail | no |
| Subject briefs: the agent's private working notes per client or project | `_meta/state/private/briefs/` on the person's machine | no |
| Facts and verbatim passages released to the brain or a project, and pages people signed | `knowledge/` in the brain or the project space | yes |
| The question log: what the person asked and which pages answered | `_meta/questions.log` in that space; it never leaves it | yes |
| `personal/`, `academic/`, `_scratch/` | the person's machine | no, in every space |

Before writing a record, the agent decides whether it is work, personal, academic or unsure.
Personal and academic records are counted in the run log and written nowhere. An unsure record
waits on the person's machine until they answer yes (it comes in as work) or no (it is
dropped); unanswered, it goes after 14 days. This is an agent's judgment and can be wrong.

Mail and calls name other people: colleagues, clients, suppliers. Their words and addresses are
copied with the record.

Who else processes the data:

- **Your Git host** (GitHub in this template) stores every repository and runs the checks on
  every pull request. The checks read the brain and other mounts with a read token the owner
  provides.
- **The model provider** behind the agent reads whatever the agent reads. `models:` in
  `space.yaml` names the providers allowed to read a space and the generated `AGENTS.md` tells
  any other model to stop. That is an instruction to the agent, not a technical barrier.
- **The connector providers** (mail, chat, call recorder, calendar, documents) are where the
  records come from; the agent reads them with the person's own grants.

The template's authors receive nothing. There is no telemetry and no hosted service. The tools
talk to your Git host; setup also installs PyYAML from PyPI and, where they are missing, `gh`,
`uv` and Git from their official releases.

## Who is responsible

The company that runs the brain decides what is processed and why, so it is the data
controller. The Git host, the model provider and the connector providers process data for it,
under the terms you have with each of them.

## Who can read what

| Space | Audience in `space.yaml` | What it holds |
|---|---|---|
| The brain | everyone registered in the company | released facts, signed pages, the registry |
| A person's workspace | that person | their records, drafts, journal and question log |
| A walled project space | its crew | what was released to the project, its own channel, tracker and repository records |
| A corridor between two spaces | exactly the people of both | requests and answers between them |

The audience is what the agents work to. Who can actually open a repository is whatever the
Git host grants, and the tools never change it. The daily chain compares each repository's
readers on the host with its audience and puts every mismatch on the owner's Needs-you list.
On GitHub, the owners of an organization can read every repository in it, including
workspaces created there; a workspace in a person's own account is readable by that person
and whoever they add.

The brain is mounted read-only in every workspace and project, and a project space is mounted
in each crew member's workspace. Those mounts are checkouts on the crew's machines.

What may leave a workspace is decided per record: an agent reads it in full and decides, a
second agent checks every decision that sends anything out, and the tools enforce facts (a
secrets scan, passages quoted exactly, the destination's audience). Nothing about a client,
project or deal leaves the space it came into until its owner's agent has classified it
`company`, `walled` or `owner`. The registry in the brain holds each classification with
the kind and date of any obligation seen (for example "confidentiality agreement, signed
2026-04-14"), never its wording. A client's name and class are therefore readable
company-wide; a client whose very existence is confidential is not covered by this.

`rules.md` sections 1 and 6 and `docs/shape-1.md` give the full rules.

## How long it is kept

- Witness pages, signed pages, the registry and the question log have no expiry. Nothing
  deletes them on a schedule.
- Status pages, the morning view and the health line are rebuilt from the records on every
  run.
- Unsure records go after 14 days without an answer.
- Raw feed files stay on the person's machine until they remove them.

Git keeps history. A file deleted today is still in every earlier commit, in every clone of
the repository (crew checkouts, mounts, the release clones under `~/.spaces/.release/`) and
in the host's pull requests. Removing something for good means rewriting history in every
repository that holds it and replacing every clone. GitHub's guide to removing sensitive data
from a repository describes the steps and what needs their support.

## Removing a person

`python _meta/tools/offboard.py <workspace> --online`, run in a session with the person who is
leaving (or the owner of the parent space after they left):

- lists what still waits to leave or to be signed, and moves nothing;
- removes the sync task and the user-level instruction from their machine;
- archives the workspace repository on the host, read-only;
- marks the subjects that live in the workspace archived in its registry;
- deletes no file.

Their raw records (feed files and witness pages) are removed only with
`offboard.py --forget-records`, the person's own command, and git history still keeps them.
What they released to the brain or a project stays there with its signature. Removing their
access to the brain and the projects is a step on the Git host that a person with admin
rights takes; agents never change access.

## Removing a client

`python _meta/tools/forget.py <space> list <subject id>` lists everything one space holds about
a client or project: its registry entry, its folders and pages, status page, desk folders,
witness pages and candidates, queued, held and landed items, question-log lines and raw feed
lines carrying its identifiers. It changes nothing. Run it in every space that may hold the
client (each workspace, the brain, the project spaces); a person then decides what to delete.
There is no erasure tool yet, and a full erasure is the history rewrite described above.

To keep a client's material where it came in from the start, classify it `owner`.

## What to check with counsel

- Your lawful basis for copying work mail, chat and calls, including the words of colleagues,
  clients and other third parties, and whether employee monitoring or works council rules
  apply where your people work.
- Consent and notice for recording and transcribing calls.
- Processing agreements with your Git host, your model provider and each connector provider,
  and any transfer of personal data outside your region.
- Whether you need a data protection impact assessment before rollout.
- Retention periods per kind of record, and how you will answer access and deletion requests
  given that git keeps history.
- Your clients' contracts: confidentiality, data processing terms, and whether their material
  may be copied into your repositories at all. The classification gate keeps material where it
  came in until someone decides; it is not a contract review.
- Who in your organization can read every repository (organization owners on GitHub), and
  where the repositories are hosted.
