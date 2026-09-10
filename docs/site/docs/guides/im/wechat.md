---
sidebar_position: 7
title: WeChat (iLink)
---

# Connect a personal WeChat bot

CubePlex connects to personal WeChat through iLink: an app that bridges your WeChat bot to
`ilinkai.weixin.qq.com` over a long connection. You do not need a public callback URL, but the
API service must be able to reach `ilinkai.weixin.qq.com` on port 443.

This guide covers personal WeChat only. WeCom (Enterprise WeChat) AI Bots have their own
[WeCom guide](./wecom.md).

## Set up iLink

1. Open <https://ilinkai.weixin.qq.com/> and create the iLink app and bot described by the
   prerequisite checklist in the wizard.
2. Keep the bot running on the device you scan with; CubePlex talks to the same bot session.

![Placeholder for the iLink bot setup screen](/img/im/wechat-ilink-placeholder.svg)

## Bind it to CubePlex

1. Open **Workspace settings → IM** and choose **Connect → WeChat**.
2. Complete the prerequisite checklist.
3. The wizard shows a **QR code** and a **binding code** with a countdown. The QR is valid for
   about two minutes; the wizard refreshes it before it expires.
4. Scan the QR code with the WeChat app that belongs to your iLink bot.
5. Then send the bot:

   ```text
   /connect <code>
   ```

   The bot replies that the binding succeeded. The account is now enabled and messages reach
   that WeChat user.

If the QR or the code expires, click **Refresh** in the wizard and repeat the scan and
`/connect` step with the new values.

## Use the bot

- Send the bot a message in a direct chat to start an agent run.
- Replies stream into the chat while the reply window is available; longer or delayed runs fall
  back to a final message.

## Current limits

This connector supports personal WeChat via iLink only. It does not support WeCom AI Bots,
callback-mode enterprise apps, or interactive in-chat approval and question controls; continue
those pending-input steps in the CubePlex web UI.
