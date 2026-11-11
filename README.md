# Craigslist Mover Alerts

Scans Craigslist gig sections for mover jobs and sends a push notification to your phone via [Pushover](https://pushover.net). Standard library only, no installs.

## Setup
1. Install the **Pushover** app on your phone (one-time purchase after a 30-day trial) and log in at pushover.net.
2. Copy your **User Key** from the Pushover dashboard.
3. Create an application at pushover.net/apps/build and copy its **API Token**.
4. Put both in `.env` as `PUSHOVER_USER_KEY` and `PUSHOVER_APP_TOKEN`.
5. Run `python3 craigslist_movers.py`. The first pass only records existing posts, so you won't get flooded.

## Running
The script loops forever, checking every `poll_interval_minutes` (default 10). Leave the terminal open, or run it in the background:

    nohup python3 craigslist_movers.py >> run.log 2>&1 &

Stop it with Ctrl+C, or `pkill -f craigslist_movers.py` if backgrounded. It stops if the machine sleeps or reboots, so restart it afterwards.

## Config
- `sections`: `lbg` labor gigs, `dmg` domestic gigs, `ggg` all gigs, `mov` moving services
- `queries`: search terms
- `exclude_words`: skip posts whose title contains these
