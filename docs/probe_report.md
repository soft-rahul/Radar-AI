# Phase 1 probe report
_Run: 2026-10-06T11:42:33+00:00_

- Plan: **Free Plan** · searches left before: **250** · after: **242** · searches sent by probe: **8**
- Locations: {"Mumbai": ["Mumbai,Maharashtra,India", "Navi Mumbai,Maharashtra,India", "Mumbai Suburban,Maharashtra,India"], "Delhi": ["Delhi,Delhi,India", "Delhi,Ohio,United States", "Delhi,New York,United States"]}
- Gemini: **gemini-3.5-flash: ok ('OK')** (re-checked after fix; gemini-2.5-flash is closed to new keys)

| Variant | Engine | Status | HTTP | Text chars | Refs | Organic | Brands named | Secs | Notes |
|---|---|---|---|---|---|---|---|---|---|
| mumbai-en | google + AI Overview | ok | 200 | 1467 | 23 | 9 | MuscleBlaze, Optimum Nutrition, AS-IT-IS, Nakpro | 4.4 | - |
| mumbai-en | Google AI Mode | ok | 200 | 3343 | 0 | 0 | MuscleBlaze, Optimum Nutrition, AS-IT-IS, Nakpro, HealthKart, Atom | 7.5 | - |
| mumbai-en | Bing Copilot | ok | 200 | 2405 | 11 | 0 | MuscleBlaze, HealthKart, Nutrabay, Atom | 26.6 | no location/hl sent (Bing params differ) |
| delhi-hi | google + AI Overview | ok | 200 | 1474 | 3 | 9 | MuscleBlaze, Optimum Nutrition, AS-IT-IS | 2.0 | AIO deferred -> page_token follow-up |
| delhi-hi | Google AI Mode | ok | 200 | 3200 | 2 | 0 | MuscleBlaze, Optimum Nutrition, Nakpro | 6.5 | - |
| delhi-hi | Bing Copilot | ok | 200 | 1834 | 10 | 0 | - | 11.4 | no location/hl sent (Bing params differ) |
| mumbai-en | Google AI Mode repeat | ok | 200 | 15083 | 16 | 0 | MuscleBlaze, Optimum Nutrition, MyProtein, Nakpro, GNC, Isopure, HealthKart | 10.3 | - |

**Variability (AI Mode EN, two no_cache runs):** identical text: False · identical brand set: False
(run 1 brands: ['MuscleBlaze', 'Optimum Nutrition', 'AS-IT-IS', 'Nakpro', 'HealthKart', 'Atom'] · run 2 brands: ['MuscleBlaze', 'Optimum Nutrition', 'MyProtein', 'Nakpro', 'GNC', 'Isopure', 'HealthKart'])

**AI engines working for India:** ['Bing Copilot', 'Google AI Mode', 'google + AI Overview'] → **GO**
