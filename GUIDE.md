# Guide: how this code works

A plain-English tour, so you can find your way around and change things safely.

## What it does

It reads live sensor data from the StrideMate exoskeleton, works out walking measurements (steps per minute, stride time, how even the two legs are), and shows them on a web page. With `tunnel.sh` that page gets a public link you can send to anyone.

It only reads. Nothing in here can move the motors.

## Run it

1. `python3 -m exotel --demo`
2. Open the link it prints.

No installs needed: it uses only Python's built-in libraries. The demo makes up realistic walking data, so no hardware is needed.

## The big picture

```
data source          ->  Hub                ->  web page
(demo, live leg,         works out the          gets new numbers pushed
 or a recording)         walking numbers        to it about 20 times a second
```

1. A **source** produces one reading at a time (both legs' distance sensors, motor power, battery).
2. A background thread hands each reading to the **Hub** (`server.py`), and saves it to a file if you used `--record`.
3. The Hub feeds each leg's distance into a **GaitTracker** (`gait.py`), which spots each stride and keeps the averages.
4. Every open web page receives the reading plus the latest numbers.

## Where things live

| File | What it does | Open it when you want to... |
|---|---|---|
| `exotel/__main__.py` | The command line flags, and starts everything | add a flag |
| `exotel/sources.py` | The three data sources: demo, live HTTP, replay a file | change the fake data, or how the real leg is read |
| `exotel/gait.py` | Finds strides in the distance signal and does the maths | change a measurement or add a new one |
| `exotel/server.py` | The small web server and the access token check | add a page or change security |
| `exotel/web/index.html` | The dashboard page (charts and numbers) | change how it looks |
| `tunnel.sh` | Starts the dashboard plus a Cloudflare public link | change how sharing works |
| `sample/session.jsonl` | A 20 second recording to replay | |
| `tests/`, `scripts/smoke_test.py` | Checks that everything still works | after any change |

## How a stride is found

The distance sensor's number goes up and down as the leg swings. The tracker keeps the recent highest and lowest values and draws a middle line between them. Each time the signal climbs clearly above that line, that is one stride. The time between strides gives everything else:

- **Stride time**: average seconds between strides.
- **Cadence**: steps per minute, `120 / stride time` (one stride of one leg is two steps).
- **Stride CV**: how much stride time varies, as a percentage. Lower is steadier.
- **Asymmetry**: how different the two legs' stride times are, as a percentage.

Numbers stay at 0 until there are at least 6 strides, so it never shows something meaningless.

## Common changes

**Make the demo walk faster or slower.** Change `period = 1.10` (seconds per stride) in `DemoSource.__iter__` in `sources.py`. The `1.035` just below makes the left leg a little slower, so asymmetry has something to show.

**Add a new measurement.** Calculate it in `GaitTracker.metrics()` in `gait.py`, add it to `GaitMetrics` and its `as_dict()`, then show it in `web/index.html`.

**Change the dashboard look.** Everything is in `web/index.html`: styles at the top, layout in the middle, script at the bottom.

## Check you didn't break anything

```
python3 -m unittest discover -s tests -t . -v
python3 scripts/smoke_test.py
```

## Words you will see

- **Source**: anything that produces readings, one at a time.
- **SSE (Server-Sent Events)**: the web page keeps one connection open and the server pushes new readings down it.
- **Token**: a random password in the link (`?k=...`). Without it, the server refuses every request.
- **Dropout**: the sensor lost its target for a moment. It is sent as `null`, never as 0, so it can't fake a big swing.
- **Hysteresis**: a small band around the middle line, so noise near the line isn't counted as extra strides.
