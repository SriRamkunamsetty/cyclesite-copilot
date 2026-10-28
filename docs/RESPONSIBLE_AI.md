# Responsible AI notes

## What this system is for

Decision support for choosing candidate shop locations. It supplies evidence and says how weak
that evidence is. It does not decide, and it never recommends signing a lease.

## Known limits (also stated in every memo where relevant)

1. **Old data.** The public Citi Bike table ends in 2018. The agent must say so and require the
   user's agreement before using data older than three years. Real decisions need fresher trips,
   footfall or sales data.
2. **Bikeshare riders are not all cyclists.** They skew toward commuters and tourists in places
   where bikeshare exists. In cities without bikeshare, demand is modelled and flagged.
3. **Demand is not revenue.** Units are modelled bike trips per year.
4. **Rideability is a design choice.** Its weights are set by hand. The backtest checks whether it
   helps predict real usage and the memo reports the answer, including "no reliable lift".
5. **Map data is uneven.** OpenStreetMap coverage varies by city and neighbourhood. Thin coverage
   makes a place look worse than it is; this can bias recommendations toward well-mapped areas.
6. **Uncertainty is understated.** The low/high range captures month-to-month noise only, not
   growth, closures, weather years or policy changes.
7. **Possible fairness effect.** Optimising for cyclist-friendly, well-mapped, high-footfall places
   can steer investment toward already well-served neighbourhoods. A person should weigh that.

## Safeguards in the design

- Numbers come from code, not the model; the memo is built from stored results.
- Expired or unknown-age data blocks the analysis until the user agrees.
- Synthetic data is labelled at the top of every memo and in the prompt.
- A critic agent and a deterministic red-team list present reasons the pick may fail.
- Read-only database access, a cost cap, an audit trail, and no write roles for the runtime.
- No personal data is used: trips are aggregated per station, and shop CSVs contain business
  locations only. Do not put customer data in the shops file.

## Before using in production

Replace or supplement the public trips with current data, validate the rideability weights against
local evidence, run the evaluation set against the exact model you deploy, and have a person with
local knowledge visit the shortlisted sites.
