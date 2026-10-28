// The Help & FAQ page's content (pages/help/): short guides, one per part
// of the platform, each with one example, and the frequent questions. The
// examples share one made-up story: a `word_count` function, a `Counter`
// object named "visits", a "count-words" choreography fed by the bucket
// "you/texts" and the event "text.added".

export interface GuideSection { title: string; text: string[]; code?: string }
export interface Guide { id: string; title: string; icon: string; summary: string; sections: GuideSection[]; next?: string[] }
export interface Faq { q: string; a: string[] }

export const MADE_UP_NOTE = 'Names in the examples (word_count, visits, count-words, you/texts) are made up: use your own.'

export const GUIDES: Guide[] = [
  {
    id: 'getting-started',
    title: 'Getting started',
    icon: 'mdi-rocket-launch-outline',
    summary: 'Rent a VE, make an API key and run your first function from Python.',
    sections: [
      {
        title: '1. Rent a VE',
        text: [
          'A VE (virtual environment) is your own space on the platform: your functions, objects and choreographies run there.',
          'Open VEs in the menu and choose Rent a VE. The Free plan is enough to try everything.',
        ],
      },
      {
        title: '2. Make an API key',
        text: [
          'Your programs use an API key instead of your password. Open the account menu (top right), choose API keys, then New key.',
          'The key is shown only once: copy the three lines it gives you into your terminal.',
        ],
        code: `export AXO_API_KEY=axo_...           # your key, shown once
export AXO_VE=<your VE id>
export AXO_API_URL=<the platform address>`,
      },
      {
        title: '3. Install the library and run a function',
        text: [
          'Mark a Python function with @axo_function. Calling it with the client runs it on your VE as a job; calling it directly runs it on your computer.',
        ],
        code: `pip install axo

from axo import AxoClient, axo_function

@axo_function
def word_count(text: str):
    return {"words": len(text.split())}

client = AxoClient()                                  # reads AXO_API_KEY / AXO_VE
job = client.call(word_count, text="hello axo").unwrap()
print(job.result().unwrap())                          # {'words': 2}`,
      },
      {
        title: '4. See it in the app',
        text: ['Open your VE: the function is under Functions and its job under Jobs, with its time, result and cost.'],
      },
    ],
    next: ['functions', 'objects'],
  },
  {
    id: 'functions',
    title: 'Functions and jobs',
    icon: 'mdi-function-variant',
    summary: 'Code that runs on your VE; every call is a job.',
    sections: [
      {
        title: 'What it is',
        text: [
          'A function is plain Python marked with @axo_function. Each call is a job: it waits for a free runner, runs, and keeps its result for a while.',
          'Parameters go by name and must be JSON values (numbers, text, lists, dicts); so does the result. Big data goes through buckets.',
          'A new version is saved only when the code changes.',
        ],
      },
      {
        title: 'Example',
        text: ['Packages or another Python version run the function in its own container.'],
        code: `from axo import AxoClient, axo_function

@axo_function(python_version="3.12", requirements=["numpy"], max_concurrency=4)
def word_count(text: str, ctx=None):
    words = len(text.split())
    if words == 0 and ctx:
        ctx.warn("empty text")                        # shows on the job
    return {"words": words}

client = AxoClient()
job = client.call(word_count, text="one two three", keep_result="7d").unwrap()
print(job.result().unwrap())`,
      },
      {
        title: 'In the app',
        text: [
          'Functions → New function registers code from the browser. A function\'s Run tab runs it with a form and shows the same call as Python.',
          'Jobs lists every job; a failed one can be retried, a running one cancelled. Only failures caused by your code are billed.',
        ],
      },
    ],
    next: ['choreographies', 'buckets'],
  },
  {
    id: 'objects',
    title: 'Active objects',
    icon: 'mdi-shape-outline',
    summary: 'Classes whose objects keep their state between calls.',
    sections: [
      {
        title: 'What it is',
        text: [
          'Subclass Axo: an object has a name in your VE and keeps its public attributes (self.count) between calls.',
          'Calls on one object run one at a time. A call that fails changes nothing. Every saved change is a revision you can go back to.',
        ],
      },
      {
        title: 'Example',
        text: ['"run" is the default method; others are marked with @axo_method.'],
        code: `from axo import Axo, AxoClient, axo_method

class Counter(Axo):
    def __init__(self, start: int = 0):
        self.count = start

    def run(self, by: int = 1):
        self.count += by
        return self.count

    @axo_method
    def reset(self):
        self.count = 0

client = AxoClient()
client.register_class(Counter)
visits = client.create(Counter, "visits", 10).unwrap()
print(visits.run(by=5).unwrap().result().unwrap())    # 15
client.object("visits").reset()                       # later, from anywhere`,
      },
      {
        title: 'In the app',
        text: [
          'Objects shows every object and, in the Classes tab, every class. An object\'s page shows its state, its calls and its revisions (Restore goes back to one).',
          'A new class version doesn\'t move objects by itself: Upgrade runs the new class\'s upgrade(self, old_version).',
        ],
      },
    ],
    next: ['choreographies'],
  },
  {
    id: 'choreographies',
    title: 'Choreographies',
    icon: 'mdi-sitemap-outline',
    summary: 'Steps connected by arrows: each step\'s answer goes to the next.',
    sections: [
      {
        title: 'What it is',
        text: [
          'A choreography joins functions, objects, buckets and other choreographies. A step starts when the steps before it are done and gets their answers as parameters.',
          'Arrows can have conditions ("only if rows > 1000"); a red arrow runs only when its step fails. Every save is a new version.',
        ],
      },
      {
        title: 'Example',
        text: ['Every new file in the bucket you/texts starts a run that counts its words.'],
        code: `from axo import AxoClient, Choreography
# word_count: the function from the Functions guide

client = AxoClient()
ch = Choreography("count-words")
texts = ch.bucket("you/texts")                        # its files are the input
count = ch.step(word_count)
texts >> count
ch.on_bucket("you/texts", prefix="new/")              # a new file starts a run

client.save(ch)
run = client.run(ch).unwrap()
print(run.result())`,
      },
      {
        title: 'In the app',
        text: [
          'Choreographies → New choreography opens the designer: drag steps from the Add panel and connect them. Problems show on the canvas while you draw.',
          'A run\'s page animates its steps live; a finished one can be replayed or resumed from what failed.',
        ],
      },
    ],
    next: ['events', 'buckets'],
  },
  {
    id: 'buckets',
    title: 'Buckets and files',
    icon: 'mdi-database-outline',
    summary: 'Where your data lives: files with versions, shared by name.',
    sections: [
      {
        title: 'What it is',
        text: [
          'A bucket holds files. Writing a file with the same name again makes a new version; reading gets the newest one.',
          'Buckets are named owner/bucket (you/texts) and can be shared with people or teams.',
        ],
      },
      {
        title: 'Example',
        text: ['Inside an object (or a choreography step) self.store reads and writes the buckets it is connected to.'],
        code: `from axo import Axo

class Counter(Axo):
    def run(self, by: int = 1):
        text = self.store.read("new/today.txt", bucket="you/texts", format="raw").decode()
        self.store.write("counts/today", {"words": len(text.split())}, bucket="you/texts")`,
      },
      {
        title: 'In the app',
        text: [
          'Buckets (main menu) lists yours, your teams\' and those shared with you. Upload by dragging files or folders; downloads of many files come as one zip.',
          'A deleted bucket is kept 30 days and can be restored from the Deleted tab. Storage is billed per calendar month.',
        ],
      },
    ],
    next: ['choreographies'],
  },
  {
    id: 'events',
    title: 'Events and triggers',
    icon: 'mdi-lightning-bolt-outline',
    summary: 'Start choreographies when something happens.',
    sections: [
      {
        title: 'What it is',
        text: [
          'An event is a small message with a type ("text.added") and data. A choreography\'s trigger starts a run for every event of its type, optionally only when the data matches a condition.',
          'The platform sends file.written by itself when a file is written in a bucket.',
        ],
      },
      {
        title: 'Example',
        text: ['The same id twice is one event, so sending again after an error is safe.'],
        code: `from axo import AxoClient, Choreography
# word_count: the function from the Functions guide

client = AxoClient()
ch = Choreography("count-words")
count = ch.step(word_count)
ch.on_event("text.added", starts=[count])            # the event's data = the run's input
client.save(ch)

client.emit("text.added", {"text": "hello axo"}, id="text-1")`,
      },
      {
        title: 'In the app',
        text: ['A VE\'s Events page lists the event types, who uses them and the latest events; you can send one from there too.'],
      },
    ],
    next: ['choreographies'],
  },
]

export const FAQ: Faq[] = [
  {
    q: 'Why did my VE go to sleep?',
    a: ['A Free VE with no work for 30 minutes sleeps to save room. The next job wakes it: jobs sent while it sleeps are kept and run in order once it\'s awake.'],
  },
  {
    q: 'My API key stopped working. Why?',
    a: [
      'It expired, it was cancelled, or your own role in the VE went down: a key never has more than your current role.',
      'Check the API keys page (account menu): it shows when each key expires and was last used.',
    ],
  },
  {
    q: 'What can Viewers, Developers and Admins do in a VE?',
    a: [
      'Viewers see everything but change nothing. Developers also register code and run jobs and choreographies. Admins also run the VE itself: start, stop, settings and people. The Owner can also hand it over or delete it.',
    ],
  },
  {
    q: 'When is a bill closed?',
    a: [
      'A VE bill covers one rental period, counted from the day you rented it. A storage bill covers a calendar month (UTC). Each closes an hour after its period ends and never changes after that; anything arriving later goes on the next bill as late usage.',
      'Nothing is charged to a card yet: bills are information only.',
    ],
  },
  {
    q: 'Do failed jobs cost money?',
    a: ['Only when your code failed. Platform failures and jobs that waited too long in the queue are free; a cancelled job is billed for the time it ran.'],
  },
  {
    q: 'Why is my job waiting?',
    a: ['Every runner of the function is busy, or the VE is waking up. A job\'s max wait says how long it may wait before failing; the function\'s settings set how many runners it may use at once.'],
  },
  {
    q: 'How long are job results kept?',
    a: ['As long as the job asked (1 hour up to forever), within the most your VE settings allow. The job stays listed until its result is deleted.'],
  },
  {
    q: 'I deleted a bucket by mistake. Can I get it back?',
    a: ['Yes, for 30 days: Buckets → Deleted → Restore brings back its files, aliases and access.'],
  },
  {
    q: 'What does an error name like BUCKET_NOT_FOUND mean?',
    a: ['Every error has a name and a number. The error catalog explains each one and what to do; error messages in the app link straight to their entry.'],
  },
]
