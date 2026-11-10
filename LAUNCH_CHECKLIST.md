# Fetchly launch checklist

Everything the code can do for search and sharing is already built. The steps below need **your** accounts, your
domain and a live server, so they cannot be done from inside the project. Do them in this order.

## 1. Already built into the site

| What | Where |
|---|---|
| Link preview (image, title, description) for WhatsApp, X, Facebook, LinkedIn, Telegram | `templates/_head.html`, `static/og-image.jpg` |
| Seven dedicated tool pages and a rights and privacy page, each with its own title, description, headline, steps and questions | `pages.py`, `templates/service.html` |
| Pages linking to one another: menu, tool grid on the home page, "More tools" on every tool page, breadcrumbs, footer | the templates |
| `robots.txt` and `sitemap.xml`, made from the real list of pages | `app.py` |
| Structured data (WebSite, WebApplication, FAQPage, BreadcrumbList) | `app.py` |
| Verification codes for Google Search Console and Bing, switched on by settings | `.env` |
| Google Tag Manager or Google Analytics 4, loading only after a visitor clicks Allow | `.env`, `templates/_analytics.html` |
| Speed: one 32 KB self-hosted font, no third-party files until a visitor agrees, pages of about 25 KB, long caching for static files, compression | everywhere |
| `noindex` on the per-visitor pages (`/options/...`, `/api/...`, `/file/...`) | `app.py` |

Public pages stay readable even when you set an `ACCESS_CODE`, so link previews and search engines still work.
Only the tool (`/api`, `/options`, `/file`) asks for the passcode.

## 2. Before you go live

1. Rent the server, point your domain's A record at it, and follow the deploy steps (`docker compose up -d --build`).
2. In `.env` set `DOMAIN` and `SITE_URL` (same domain, with `https://`). Without `SITE_URL` the site builds links from the address it is opened on, which also works behind Caddy.
3. Set `CONTACT_EMAIL` so the rights and privacy page shows how an owner can ask for a link to be blocked.
4. Open these and check they load: `/`, `/robots.txt`, `/sitemap.xml`, and one tool page such as `/youtube-video-downloader`.
5. Run `python tests/test_site.py` on your computer before each deploy.

## 3. Test the link preview

Paste your home page link into each of these. The picture, the title and a short line should appear.

- WhatsApp: send the link to yourself or a friend.
- Facebook Sharing Debugger (developers.facebook.com/tools/debug): paste the link, press Scrape Again.
- LinkedIn Post Inspector (linkedin.com/post-inspector).
- X: paste the link in a new post (do not send it).

Apps remember the first preview they saw. If you change `static/og-image.jpg` later, use the Facebook and LinkedIn tools to refresh it.
To redraw the image: `python tools/make_assets.py`.

## 4. Google Search Console

1. Go to search.google.com/search-console and add your site. The **Domain** option is best.
2. Verify. For a Domain property Google gives you a DNS TXT record to add at your domain registrar. For a URL-prefix property choose the HTML tag option, copy only the `content` value, put it in `.env` as `GSC_VERIFY`, redeploy, then press Verify.
3. Open **Sitemaps** and submit `sitemap.xml`.
4. Open **URL inspection**, paste the home page, then each tool page, and press **Request indexing**.
5. Come back after a few days: **Pages** shows what is indexed, **Performance** shows searches and clicks.

## 5. Bing Webmaster Tools

1. Go to bing.com/webmasters and sign in.
2. Easiest: choose **Import from Google Search Console**. It brings the site and its sitemap with it.
3. Otherwise add the site, choose the meta tag option, put the code in `.env` as `BING_VERIFY`, redeploy, and press Verify. Then submit `sitemap.xml`.

## 6. Google Tag Manager and Google Analytics

1. Create a Google Analytics 4 property (analytics.google.com) and copy its Measurement ID (`G-...`).
2. Create a Tag Manager container (tagmanager.google.com), type Web, and copy its ID (`GTM-...`).
3. In Tag Manager add a **Google tag** with your `G-...` ID, trigger **All Pages**, and Publish.
4. Put the `GTM-...` ID in `.env` as `GTM_ID` and redeploy. If you do not want Tag Manager, set `GA_ID` instead and leave `GTM_ID` empty. Never set both.
5. Visit your site, click **Allow** on the notice, and check GA4 **Reports > Realtime** and Tag Manager **Preview**.

Nothing loads until the visitor allows it. The privacy page already says so.

## 7. Google Business Profile: probably not for this site

A Business Profile is for businesses with a physical location or that serve customers face to face in a service area.
Fetchly is an online tool, so it does not qualify, and inventing an address breaks Google's rules and gets the profile
suspended. If you later run a real business with a real address (for example a repair shop), set one up then.
For an online tool the equivalents are Search Console, Bing, and the directories in step 9.

## 8. Speed check (GTmetrix and PageSpeed Insights)

After launch, test the home page and one tool page at gtmetrix.com and pagespeed.web.dev.
Expect small pages and no third-party files before consent. If a score is low, look at:

- the **server location**, because a distant server is slower for visitors in Kenya,
- whether Caddy is compressing (check "Content-Encoding" in the response headers),
- the analytics settings, since Tag Manager adds weight once visitors allow it.

## 9. Getting listed

Search engines find you through the sitemap. People find you through these, in rough order of effort:

1. A clear GitHub README with a screenshot, and repository topics such as `video-downloader`, `yt-dlp`, `flask`.
2. Tool directories: AlternativeTo, Product Hunt, and similar lists. Some refuse downloader tools, so read each one's rules first.
3. Helpful posts or answers in communities where people ask how to save their own videos. Follow each community's rules and do not spam.
4. Keep improving the words on each tool page. Pages with real answers earn links over time.

Be ready for one reality: a public site that saves other people's videos can receive takedown notices, and hosts and
search engines act on them. Keep the rights notice visible, keep `ALLOWED_HOSTS` to the sites you test, and answer
owner requests quickly.

## 10. Settings reference (`.env`)

| Setting | What it does |
|---|---|
| `DOMAIN` | the domain Caddy gets a certificate for |
| `SITE_URL` | `https://` plus the same domain; used in previews, canonical links and the sitemap |
| `ACCESS_CODE` | passcode for the tool only |
| `GSC_VERIFY`, `BING_VERIFY` | verification codes (only the `content` value) |
| `GTM_ID` or `GA_ID` | analytics, loaded only after consent |
| `CONTACT_EMAIL` | shown on the rights and privacy page |

## 11. Editing the pages

Open `pages.py` to change the words of a tool page, or to add one: copy a block, give it a new `slug`, a unique
`title` (under 60 characters) and `description` (under 160), and list two or three `related` slugs. The sitemap, the
menu, the home page grid and the footer pick it up by themselves.
