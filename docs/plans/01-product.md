# Product: tomabotchi

## Problem
"I start a few agents and then lose track of them. An hour later I find one has been
sitting there the whole time waiting for me to OK a tool. Another has been retrying the
same failed search over and over. To find out what any of them is actually thinking I
have to dig through terminals and trace pages. When I want to check the code or files an
agent produced, I have to go hunting for where it put them. And each one's personality
and rules live in a prompt file I never get round to updating, even after it does
something I didn't like."

## Success metric
**Median time an agent spends waiting on you**: from the moment a pet asks for permission
(a "call") to the moment you answer it.
- Measured by tomabotchi from the timestamps of each call and its answer, and shown on the
  shelf as a weekly figure.
- Target: **under 2 minutes** while you're at your desk. The first week sets the baseline.
  Today nothing measures this, and it's probably tens of minutes.
- Watched alongside it, but not the metric: how long a stuck pet goes unnoticed.

## Announcement — the blog post before the feature
Meet tomabotchi, a pocket pet for every AI agent you run. Hatch an agent, give it a name
and a shell colour, and shuffle until you find a look you love: no two pets are drawn
the same. Answer a few questions to write its soul: who it is, how it talks, what it
must ask you about first and what it must never do. Every pet lives in its own sealed
home, and you can open that home in VS Code right inside the dashboard to review or
change what it's built. Then give it a task and watch it work on a little LCD screen.
It shows its current thought, which step it's on and the tools it's using. When it
needs your permission it beeps, in your browser if you like, and wobbles on the shelf
until you answer. Say no and tell it why, and that lesson is written into its soul.
Nobody dies in tomabotchi: retired pets rest on the memory shelf with their soul and a
backup of everything they made.

## Scope of v1
- v1 is for **pets you hatch in tomabotchi**.
- Adopting agents or sessions you already run elsewhere is for the next version.

## How pet care maps to agent care
| On the toy | In tomabotchi |
|---|---|
| Attention icon beeps | The pet needs a decision from you: a tool approval or a question |
| Feed | Give it a task |
| Hunger hearts | Whether it has work. Idle pets get hungry |
| Energy | Budget left (tokens, time) for the current task |
| Happy hearts | Whether its recent steps are going well |
| Health / sick skull | Errors or loops. A sick pet is stuck |
| Medicine | Unstick it: nudge, retry or restart the step |
| Mess (droppings) | Clutter piling up in its working memory |
| Clean | Tidy its memory |
| Lights off | Pause it. Lights on resumes |
| Play | Chat with it directly |
| Discipline | Praise or scold. Both write a lesson into SOUL.md |
| Age and growth stage | Egg → baby → kid → teen → adult, as it completes tasks |
| Each toy's character is different | Each pet's look is drawn at random when you hatch it: body, head, eyes, legs, arms, mouth. Shuffle as many times as you like, and the look you pick stays with that pet for life |
| The toy dies | Nobody dies. You retire a pet and it moves to the memory shelf |

## Every pet lives in its own home
- Each pet runs in its own sealed container (its "home"). It can only see and change what's inside that home, and one pet can't touch another.
- The default home is a basic Python environment.
- When you hatch a pet you pick its home from a list, for example Python, Python with data tools, Node.js or Web browser. That list is set in tomabotchi's config on the host machine, not on the hatch screen. The settings screen shows it read-only.
- The pet screen shows how the pet's home is doing: memory, CPU, workspace size and what it can reach on the network.

## Open the workspace in VS Code
- Every pet has a Workspace tab showing VS Code open on that pet's home: its files, an editor and a terminal, so you can review and change what it made.
- The editor appears inside the dashboard. If it can't load there, "Open in a new tab" opens the same workspace straight from the machine running tomabotchi.
- The pet sees your saved changes straight away. "Pause Biscuit while I edit" stops it working until you're done.

## Retiring and the memory shelf
- Pets never die. You retire a pet by choosing Retire on its screen and confirming, with an optional reason.
- When a pet retires, its workspace is packed into a zip backup and kept in the archive folder of tomabotchi's main storage. Its soul is kept too.
- Retired pets live on a separate memory shelf screen. It lists each one with its dates, home, reason and backup, and lets you download the backup, read its soul or hatch a new pet from that soul.

## Notifications
- The shelf offers once to turn on browser notifications. The browser asks for your permission first, and "Not now" dismisses the offer.
- In settings you can turn all notifications off, or switch each kind on or off: a pet is calling you, a pet got sick, a pet finished its task, a pet ran out of budget.
- Quiet hours stop beeps overnight. Calls still wait for you.
- "Send a test beep" checks that notifications work.

## Added at Gate 2 (user-approved, 2026-10-25)
- **Tool rules editor** on the pet screen. For each kind of tool (read, search, fetch, edit, delete, move, run commands, other), choose: free to use, ask first, or never. Defaults: reading, searching and fetching are free, everything else asks first. "Always allow" on a call adds a narrower rule, for example edits inside `notes/`.
- **Markdown everywhere you type freely**: soul answers, the SOUL.md editor, teach, and deny and retire reasons. Each has a Write/Preview toggle.

## Added at Gate 3 (user-approved, 2026-10-25)
- **Pick a brain for each pet** when you hatch it: Hermes (the default, a general helper with memory and skills) or Claude Code (strongest at code). Separately, pick its model: a free, private local model or Claude. Either brain can use either model. The pet's Home panel shows its brain and model.
- **One set of rules, asked once.** Whichever brain a pet has, only tomabotchi's tool rules decide what it may do. You're never asked twice about the same action.

## Screens
- `mockups/habitat.html`: the shelf of all your pets. Who's working, who's calling you, who's sick or asleep, an empty egg to hatch a new one, the notification offer, and links to the memory shelf and settings.
- `mockups/pet.html`: one pet up close. The big egg with the classic 8-icon menu, its vitals, its home (with Retire), the call waiting for your answer, task progress, its live thinking, tools used and running numbers.
- `mockups/workspace.html`: the pet's workspace in VS Code, inside the dashboard, with "Open in a new tab" as the fallback and "Pause while I edit".
- `mockups/soul.html`: the pet's SOUL.md. Read and edit it, accept or reject proposed lessons, teach it something new, and see the timeline of what shaped it.
- `mockups/hatch.html`: hatch a new pet. Name it, pick a shell, pick or shuffle its look (six random looks at a time), answer soul questions (with a live SOUL.md preview), choose its home, brain, model and budget, and give it its first task.
- `mockups/memory.html`: the memory shelf of retired pets, with backups and souls.
- `mockups/settings.html`: notification switches and quiet hours, plus the read-only list of homes.
