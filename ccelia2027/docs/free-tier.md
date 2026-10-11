# Free-Tier Limits

Facts checked against Cloudflare's official documentation on 2026-10-09. Free quotas fail closed at their limit; on Workers Paid, usage above included allowances can be billed. The account plan therefore matters as much as the code.

| Service                |                                                                                                   Documented Free allowance |                                                                                                                  App's internal reservation |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------: | ------------------------------------------------------------------------------------------------------------------------------------------: |
| Workers                |                                                          100,000 requests/day; static asset requests are free and unlimited |                                                                                          Application budget blocks agent calls much earlier |
| Workers AI             |                                                              10,000 Neurons/day; over-limit operations fail on Workers Free |                                                               28 calls/day × 350 reserved Neurons = 9,800 Neurons of the 10,000/day ceiling |
| Browser Run            | 10 browser minutes/day; 3 concurrent Browser Sessions; 1 new session every 20 seconds; 60-second default inactivity timeout | 5 session reservations/day × 120 seconds = 600,000 ms worst-case; the full 2 minutes stays reserved even if the session ends early or fails |
| SQLite Durable Objects |                                                                    100,000 requests/day and 13,000 GB-s/day on Workers Free |     One small global counter object; at most 66 calls/day: 28 AI reservations, 5 browser reservations, 5 releases, and 28 plan consumptions |

The selected model is `@cf/meta/llama-3.2-3b-instruct`. Cloudflare lists it as an available Workers AI model and does not list it among models that require a paid billing method. The agent uses the Workers AI binding only; it has no external provider, fallback, credit-card field, auto-recharge, R2, or Pay-as-you-go code.

Internal AI Neuron values are estimates, not exact metering. Actual consumption remains subject to Cloudflare's meter and Free-account hard stop. The Worker uses Cloudflare's `@cloudflare/playwright` package to launch one short-lived Browser Session per task and explicitly closes both its context and browser. Before reserving a session, the Worker resolves the target through Cloudflare DNS-over-HTTPS. An empty A or AAAA family is allowed, but at least one address must exist and every returned IPv4 or IPv6 address must be public; lookup failures and private or reserved addresses stop before Browser Run. Playwright permits only HTTPS requests to the exact target origin using GET or HEAD; downloads, service workers, scripts, images, media, and fonts are disabled or blocked. Navigation is limited to 12 seconds, text extraction to 5 seconds, and the whole Worker-side browser task to 45 seconds. Since Browser Run sessions normally close after 60 seconds of inactivity if explicit closure fails, the internal budget reserves 120 seconds per attempt; the UI reports this reserved maximum, not a measured Browser Run duration.

Plan consumption adds at most 28 Durable Object calls per day, because every plan token comes from an AI reservation and the daily AI cap is 28 calls. The same object also serializes AI and browser reservations. Every request waits behind the others, which is a known architectural limit. It is acceptable at this volume and would need sharding before any higher limit.

The internal counter cannot detect whether an account has secretly been upgraded to Workers Paid. Do not dispatch deployment unless the Cloudflare dashboard confirms Free and no payment/billing setup. If the plan or billing state is uncertain, status is BLOCKED.

## Official Sources

- [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/)
- [Workers AI pricing and free allocation](https://developers.cloudflare.com/workers-ai/platform/pricing/)
- [Selected model](https://developers.cloudflare.com/workers-ai/models/llama-3.2-3b-instruct/)
- [Browser Run pricing](https://developers.cloudflare.com/browser-run/pricing/)
- [Browser Run limits](https://developers.cloudflare.com/browser-run/limits/)
- [Browser Run Playwright sessions](https://developers.cloudflare.com/browser-run/playwright/)
- [Durable Objects pricing](https://developers.cloudflare.com/durable-objects/platform/pricing/)
