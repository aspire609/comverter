# QuickConvert – MP3 / MP4 converter

Users upload an audio/video file, choose **MP3** or **MP4**, and download the result.
Conversion uses ffmpeg. Files are deleted as soon as the download finishes.

## Run locally

```bash
# needs ffmpeg installed (macOS: brew install ffmpeg · Ubuntu: sudo apt install ffmpeg)
pip install -r requirements.txt
python app.py            # http://localhost:8000
```

## Deploy (easiest: Docker on a host that supports it)

The included `Dockerfile` installs ffmpeg and runs gunicorn.

**Render / Railway / Fly.io / a VPS**
1. Push this folder to a GitHub repo.
2. Create a new *Web Service* from the repo and choose the **Docker** runtime.
3. Add the environment variables below, then deploy.
4. Point your domain at it (HTTPS is automatic on these hosts).

A small VPS (2 vCPU / 2 GB RAM, about $6–12/month) is enough to start. Video-to-MP4 is CPU-heavy, so
free tiers will be slow and may time out.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SITE_NAME` | QuickConvert | Your brand name |
| `CONTACT_EMAIL` | you@example.com | Shown on Privacy/Terms pages |
| `MAX_UPLOAD_MB` | 100 | Max file size |
| `MAX_PARALLEL_JOBS` | 2 | Simultaneous conversions (set to about your CPU cores) |
| `RATE_LIMIT_JOBS` / `RATE_LIMIT_WINDOW` | 15 / 3600 | Conversions per IP per window (seconds) |
| `FFMPEG_TIMEOUT` | 300 | Seconds before a conversion is cancelled |
| `TRUST_PROXY` | 1 in Docker | Needed so rate limiting sees real visitor IPs behind a proxy |
| `ADSENSE_CLIENT` | *(empty)* | e.g. `ca-pub-1234567890123456`. Turns ads on |
| `ADSENSE_SLOT_TOP` / `ADSENSE_SLOT_BOTTOM` | *(empty)* | Ad unit IDs from AdSense |

## Making money

1. **Google AdSense**: apply at adsense.google.com with your live domain. Approval usually wants a custom
   domain, original content, and Privacy + Terms pages (included). Once approved, create two display ad
   units, put the IDs in the env vars above, and redeploy. `/ads.txt` is served automatically.
2. **Traffic is the real work.** Tools like this earn through search traffic. Add pages targeting
   searches like "wav to mp3", "mov to mp4", "extract audio from video" (one page per format pair), and
   write a short, useful explanation on each.
3. **Other options later:** a paid tier with bigger files and no ads (Stripe), or affiliate links to
   related tools. Start with ads, then add a paid tier once you have steady visitors.

## Things to know

- **Only converts files users upload.** It deliberately does not download from YouTube or other sites.
  Those downloaders violate the sites' terms, get taken down, and get ad accounts banned.
- Costs scale with usage (CPU + bandwidth). Keep `MAX_UPLOAD_MB` and the rate limits sensible.
- The rate limiter is in-memory per process; for heavy traffic put Cloudflare in front of the site.
- The Privacy/Terms pages are templates, so read them and adjust them for your country.
