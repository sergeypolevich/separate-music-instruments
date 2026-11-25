# Privacy

Audio Stems is a free hobby project by Sergey Polevich (the author). It runs on your computer. This page says what
it records, what it can send, and what happens to it. Questions and deletion requests:
[GitHub issues](https://github.com/sergeypolevich/separate-music-instruments/issues).

## On your computer only

- **Your projects**: songs, tracks, the project database and logs in the `Audio Stems` folder in your user folder.
- **Usage counts** in `Audio Stems/analytics.db`: which buttons and screens you use, how long things take, and the
  errors the app met, with a random number for this folder (not linked to you). It is never sent anywhere; you can
  read it with any SQLite tool or delete the file at any time.

## What is sent, and only when you press Send

The app asks before it sends anything, shows the exact text, and sends nothing if you decline or close the window:

| When | What is sent |
|---|---|
| Once, after the installation | the app version and the system (Windows or macOS) |
| When the setup fails (in the setup window) | the version, the system, the setup step and the error text |
| «Send report» on a failed project | the version, the system, the stage that failed and the error text |

Before an error text is shown and sent, the app removes your user name, your home folder, and the song's title, link
and file names. A report has no name, no account, no e-mail address and no identifier, so reports cannot be linked
to each other or to you.

## Where reports go

Reports go to a small receiver of the author's on Cloudflare (Cloudflare Workers and a D1 database created in the
EU jurisdiction, so the data is stored in the European Union). The receiver keeps only the fields above and the day
it got the report, not the time. It does not keep your IP address: Cloudflare sees it to deliver the request, as any
website's host does, but the receiver stores it nowhere and its request logs are turned off. Cloudflare acts as the
author's processor under its [Data Processing Addendum](https://www.cloudflare.com/cloudflare-customer-dpa/).

Reports are used only to learn whether the app installs and works and to fix errors. They are not sold, shared, or
used for advertising or profiling. They are kept until the author deletes them; since a report carries nothing about
who sent it, ask in the issues with the day and the error text and that report is deleted.

## Your rights

The legal basis for a report is your consent (GDPR Art. 6(1)(a); the same applies under Russia's Federal Law No.
152-FZ). You can say no each time, and the app works the same. You may ask what is kept, ask for its deletion, or
complain to your data protection authority. Residents of US states with privacy laws (for example California) have
the same rights; no personal information is sold or shared for advertising.

## Children

Audio Stems is not directed to children under 13 and does not knowingly collect personal information from them
(COPPA). A report contains no information that identifies anyone; if you believe a child sent one with personal
details in its error text, ask in the issues and it is deleted.

## Changes

A change to this page is listed in the release notes of the version that brings it.
