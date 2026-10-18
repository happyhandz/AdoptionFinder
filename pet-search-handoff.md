# Pet search tool: handoff notes

Goal: build a tool that automatically searches for adoptable puppies near Kent, WA and alerts on new listings.

## What we found

### Petfinder
- The Petfinder API has been **decommissioned** (retired during the Petfinder Pro rebuild). Old v2 SDKs/tutorials no longer work.
- Fallback: Petfinder saved searches with email alerts on the website.
- Source: https://outreach.petfinder.com/s/article/Welcome-to-the-New-Petfinder-Pro

### RescueGroups.org
- Mainly a back-end service for rescues (websites, portals), BUT it also has a public adoptable pet data API.
- Covers every animal listed by rescues/shelters that use RescueGroups. Does NOT cover rescues that only post on Petfinder, Adopt-a-Pet, or their own sites.
- API v5 base URL: https://api.rescuegroups.org/v5
- Docs: https://api.rescuegroups.org/v5/public/docs (also https://test1-api.rescuegroups.org/v5/public/docs)
- Public data only needs an API key (sent in the Authorization header). Free key via form: https://rescuegroups.org/services/adoptable-pet-data-api/
- Supports search by postal code + radius, breed, age, size, etc. No stated caps on requests/results.
- Example endpoint: POST /public/animals/search/available/dogs/haspic

## Existing GitHub projects worth reusing

- **Sco314/dog-rescue-tracker**: https://github.com/Sco314/dog-rescue-tracker
  Python, runs on GitHub Actions, per-rescue scrapers, fit scoring, dedupe, email alerts. Best base to adapt.
- **sfierro/dog-adoption** (fork: https://github.com/jorkopp/job-adoption)
  Python, scrapes shelters, dedupes, SMS alerts via Twilio, GitHub Actions.
- **GolnazAfshinpour/PuppyFinder**: https://github.com/GolnazAfshinpour/PuppyFinder
  .NET + Vue app, RescueGroups + county feeds, ZIP/radius search, email alerts. Heavier to run.
- **pipeworx-io/mcp-rescuegroups**: https://github.com/pipeworx-io/mcp-rescuegroups
  MCP server for RescueGroups (needs your own API key).
- Simple RescueGroups front ends for reference: wo1vin/petpair, rachelrohrbach/pet-adoption

## Proposed plan

1. Request a RescueGroups API key.
2. Fork/clone dog-rescue-tracker.
3. Add a RescueGroups source (dogs, puppies, within X miles of Kent, WA 98031).
4. Add custom scrapers for local rescues not on RescueGroups (e.g. Puget Sound Rescue).
5. Run daily via GitHub Actions with email alerts for new matches.
6. Keep a Petfinder saved-search alert running alongside for coverage.
