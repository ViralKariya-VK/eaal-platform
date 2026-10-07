# How fast is the CAVY server?

Measured with `python scripts/benchmark.py --students N --markdown`. It starts a throwaway server and
has N students sign in and do a typical lab **all at the same time**: open it, type (15 edits per
stage), run code, move through the three stages, submit, and open their progress page. Every call is
timed.

Computer: a MacBook (Apple silicon), Python 3.11, with a mutation-testing job running in the
background, so these numbers are a little pessimistic. The times are for the server itself, on the
same computer as the students; on a Wi-Fi classroom add the network round trip (a few milliseconds).

## 30 students at once (a normal class)

30 students at once: 1770 calls in 2.2 s = 814 calls/s, 0 failed

| Call | Count | Median ms | 95th % ms | Slowest ms |
|---|---|---|---|---|
| get_labs | 30 | 54.8 | 71.8 | 74.2 |
| get_my_progress | 30 | 17.9 | 66.4 | 148.7 |
| get_stages | 30 | 61.0 | 82.1 | 89.3 |
| log_code_edit | 1350 | 3.9 | 44.6 | 1303.5 |
| login | 30 | 109.0 | 147.5 | 206.1 |
| prepare_run | 90 | 3.9 | 39.0 | 579.0 |
| record_run | 90 | 3.7 | 14.2 | 1396.3 |
| start_stage | 90 | 96.8 | 822.7 | 1334.2 |
| submit_session | 30 | 12.8 | 103.5 | 130.8 |

Event log: 39,155 events/s written. Running one piece of student code: median 12 ms.

## 100 students at once (a stress test)

100 students at once: 5900 calls in 7.4 s = 795 calls/s, 0 failed

| Call | Count | Median ms | 95th % ms | Slowest ms |
|---|---|---|---|---|
| get_labs | 100 | 156.7 | 268.8 | 311.3 |
| get_my_progress | 100 | 106.8 | 1044.9 | 1552.2 |
| get_stages | 100 | 222.6 | 255.9 | 264.5 |
| log_code_edit | 4500 | 60.6 | 162.5 | 3622.2 |
| login | 100 | 315.0 | 473.0 | 556.4 |
| prepare_run | 300 | 61.1 | 199.6 | 2305.1 |
| record_run | 300 | 61.9 | 116.0 | 1815.9 |
| start_stage | 300 | 293.5 | 972.7 | 2482.5 |
| submit_session | 100 | 98.9 | 441.7 | 2355.2 |

Event log: 37,163 events/s written. Running one piece of student code: median 13 ms.

## What this says

- A class of 30 gets about **800 calls a second** with **no failures**, and 100 students at once
  still finish with none. In a class of 30, typing, saving and running (the calls a student makes most) take
  a median of **about 4 ms**; the slowest moments are around a second, when everyone opens a lab or
  submits together. At 100 students the median for typing rises to about 60 ms and the worst
  calls take a few seconds.
- The event log (the record of everything a student does) writes about **38,000 events a second**,
  far more than a class produces, and it runs on its own thread so typing never waits for the disk.
- Running one piece of student code takes about **12 ms** (each run is its own process).

## A real problem this found, and the fix

The first run with 30 students showed some calls (opening a lab, the stage list) taking **30 s**, and
17 calls failed. The cause: the database connection pool held only 15 connections, and one request
can hold two or three at once (a query that calls another). With about 15 or more students working
together, requests waited on each other until the 30-second timeout. The fix, in
`src/eaal_platform/db/engine.py`: a pool of 50 connections (up to 200), a 30 s wait for the file
lock instead of failing at once, and `synchronous=NORMAL` (safe in WAL mode). After the fix: 30
students, 0 failures, and no call slower than 1.5 s. Two tests (`tests/test_db_models.py`) keep it
that way.

## Limits to be honest about

- SQLite allows one writer at a time. That is fine for a class (writes are tiny and batched), but
  one server for a whole university would need a different database.
- These numbers are for one computer acting as the server and all the students. Real laptops add
  network time. The AI assistant's answers depend on the AI company and are not included.
- Everything runs over plain HTTP on the local network (no HTTPS yet).
