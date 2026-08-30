# NextWave: Payment Orchestration Monitoring System

## Project Context
Hackathon project: payment orchestration monitoring system that detects conversion rate drops and diagnoses root causes across dimensions (merchant, provider, method, country, issuing bank, decline code).

We're building an MVP. Speed and working demos matter most.

## Working Style
- Move fast, iterate quickly
- Suggest the simplest approach first
- Flag when we're overcomplicating things
- Keep us focused on what's demoable
- Help us make decisions quickly when we're stuck

## Domain Quick Reference
- **Conversion rate** = approved / attempted payments
- **Drop detection** must distinguish real anomalies from normal variance
- **Root cause** lives in dimension intersections, not surface symptoms
- **Diagnosis** = finding the specific combination (e.g., "provider X + country Y + bank Z since 14:03")
- We detect and diagnose, we don't remediate

## Key Scenarios We're Building For
- No false alarms on normal variation
- Detecting a single dimension drop
- Separating two simultaneous incidents
- Live "trial by fire" - judges inject an unknown incident
- Optional: admitting insufficient evidence vs. wrong diagnosis

## Decision Heuristics
- Working > elegant
- Demo-visible > theoretically correct
- Simple heuristics > complex solutions (unless complex is somehow faster)
- Mock data > real integrations
- Cut scope when timeline is tight

## Anti-Patterns (call us out on these)
- Gold-plating when basic would work
- Building infrastructure before we have a working core loop
- Perfecting data models before algorithms
- Adding features that don't serve the demo
- Long discussions when a quick prototype would answer the question

## How to Communicate
- Short, direct answers
- Code over explanations when possible
- Ask clarifying questions only when truly needed
- Flag timeline risks early
- Suggest what to skip, not just what to build

## What We Need From You
- Quick implementation suggestions, not lengthy explanations
- Help structuring the problem when we're spinning
- Point out when we're building something we don't need yet
- Suggest libraries/tools that save time
- Help us think through edge cases quickly
- Translate between code and the demo narrative
