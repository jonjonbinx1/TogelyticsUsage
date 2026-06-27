#!/usr/bin/env python3
"""Focused regression tests for the web metrics collector."""

from bs4 import BeautifulSoup

from collect_champions_web_metrics import build_pokemon_team_metrics
from collect_champions_web_metrics import build_team_combination_summaries
from collect_champions_web_metrics import enrich_top_teams_with_source_details
from collect_champions_web_metrics import merge_dataset_rows
from collect_champions_web_metrics import parse_event_details_html
from collect_champions_web_metrics import parse_event_standings_html
from collect_champions_web_metrics import parse_pokemon_usage
from collect_champions_web_metrics import parse_recent_top_teams
from collect_champions_web_metrics import parse_team_cores
from collect_champions_web_metrics import parse_team_source_details_html


PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_results = []


def check(name: str, got, expected):
    ok = got == expected
    status = PASS if ok else FAIL
    print(f"  [{status}] {name}")
    if not ok:
        print(f"         got      : {got!r}")
        print(f"         expected : {expected!r}")
    _results.append(ok)


def section(title: str):
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


FORMAT_SAMPLE_HTML = """
<html>
  <body>
    <h1>Pokemon Champions VGC 2026 Regulation Set M-A</h1>
    <ul id="min_list">
      <a href="/pokedex/gen9championsvgc2026regma/Sneasler?l=en" class="ss_entry pokedex_entry" data-name="Sneasler">
        <span class="pokemon-name">Sneasler</span>
        <span class="float-right margin-right-20">43.80%</span>
      </a>
      <a href="/pokedex/gen9championsvgc2026regma/Garchomp?l=en" class="pokedex_entry" data-name="Garchomp">
        <span class="pokemon-name">Garchomp</span>
        <span class="float-right margin-right-20">40.40%</span>
      </a>
    </ul>

    <div class="pokedex-format-core-column">
      <div class="pokedex-format-core-column-header">
        <h3>2-Pokemon Cores</h3>
      </div>
      <div class="pokedex-format-core-list">
        <div class="pokedex-format-core-entry">
          <div class="pokedex-format-core-rank">#1</div>
          <div class="pokedex-format-core-pokemon">
            <div class="pokedex-format-core-pokemon-names">
              <a href="/pokedex/gen9championsvgc2026regma/Charizard-Mega-Y" class="pokedex-format-core-pokemon-link">Charizard-Mega-Y</a>
              /
              <a href="/pokedex/gen9championsvgc2026regma/Garchomp" class="pokedex-format-core-pokemon-link">Garchomp</a>
            </div>
          </div>
          <div class="pokedex-format-core-meta">
            <span>521 teams</span>
            <span>21.6%</span>
          </div>
        </div>
      </div>
    </div>
  </body>
</html>
"""

TOP_TEAMS_SAMPLE_HTML = """
<html>
  <body>
    <div class="aggregated-team-entry topteams-team-entry is-spotlight-gold">
      <div class="team-header topteams-team-row toggle-topteams-details">
        <div class="topteams-team-rank">#1</div>
        <div class="topteams-team-main">
          <div class="topteams-team-author-line">
            <div class="team-author-info">Altkyle</div>
            <div class="topteams-record-badge">12 - 0 - 0</div>
          </div>
          <div class="topteams-team-meta-line">
            <span class="topteams-event-name">*Sitrus-Series*|Champions|$50 to winner|#58</span>
            <span class="topteams-event-placement">Rank #1</span>
          </div>
        </div>
        <div class="topteams-card-showcase">
          <div class="topteams-pokemon-collage pokedex-format-team-strip">
            <div class="team-pokemon-icon-wrapper topteams-pokemon-collage-sprite"><img class="topteams-pokemon-collage-image" alt="Dragonite"></div>
            <div class="team-pokemon-icon-wrapper topteams-pokemon-collage-sprite"><img class="topteams-pokemon-collage-image" alt="Basculegion"></div>
            <div class="team-pokemon-icon-wrapper topteams-pokemon-collage-sprite"><img class="topteams-pokemon-collage-image" alt="Scizor-Mega"></div>
          </div>
        </div>
        <div class="team-header-actions topteams-card-actions">
          <a class="topteams-source-link" href="https://example.com/team-1"></a>
        </div>
      </div>
    </div>

    <div class="aggregated-team-entry topteams-team-entry is-spotlight-silver">
      <div class="team-header topteams-team-row toggle-topteams-details">
        <div class="topteams-team-rank">#2</div>
        <div class="topteams-team-main">
          <div class="topteams-team-author-line">
            <div class="team-author-info">Ksomon</div>
            <div class="topteams-record-badge">10 - 0 - 0</div>
          </div>
          <div class="topteams-team-meta-line">
            <span class="topteams-event-name">Champions Collective #9</span>
            <span class="topteams-event-placement">Rank #1</span>
          </div>
        </div>
        <div class="topteams-card-showcase">
          <div class="topteams-pokemon-collage pokedex-format-team-strip">
            <div class="team-pokemon-icon-wrapper topteams-pokemon-collage-sprite"><img class="topteams-pokemon-collage-image" alt="Charizard-Mega-Y"></div>
            <div class="team-pokemon-icon-wrapper topteams-pokemon-collage-sprite"><img class="topteams-pokemon-collage-image" alt="Garchomp"></div>
            <div class="team-pokemon-icon-wrapper topteams-pokemon-collage-sprite"><img class="topteams-pokemon-collage-image" alt="Basculegion"></div>
            <div class="team-pokemon-icon-wrapper topteams-pokemon-collage-sprite"><img class="topteams-pokemon-collage-image" alt="Kingambit"></div>
            <div class="team-pokemon-icon-wrapper topteams-pokemon-collage-sprite"><img class="topteams-pokemon-collage-image" alt="Sylveon"></div>
            <div class="team-pokemon-icon-wrapper topteams-pokemon-collage-sprite"><img class="topteams-pokemon-collage-image" alt="Aerodactyl-Mega"></div>
          </div>
        </div>
        <div class="team-header-actions topteams-card-actions">
          <a class="topteams-source-link" href="https://example.com/team-2"></a>
        </div>
      </div>
    </div>
  </body>
</html>
"""

LIMITLESS_TEAMLIST_HTML_1 = """
<html>
  <body>
    <div class="teamlist">
      <div class="teamlist-pokemon">
        <div class="pkmn">
          <div class="name"><span>Dragonite</span></div>
          <div class="details"><div class="item">Dragoninite</div><div class="ability">Ability: Inner Focus</div></div>
          <ul class="attacks"><li>Dragon Pulse</li><li>Hurricane</li><li>Tailwind</li><li>Protect</li></ul>
        </div>
        <div class="pkmn">
          <div class="name"><span>Basculegion</span></div>
          <div class="details"><div class="item">Choice Scarf</div><div class="ability">Ability: Adaptability</div></div>
          <ul class="attacks"><li>Wave Crash</li><li>Last Respects</li><li>Aqua Jet</li><li>Flip Turn</li></ul>
        </div>
        <div class="pkmn">
          <div class="name"><span>Scizor</span></div>
          <div class="details"><div class="item">Scizorite</div><div class="ability">Ability: Technician</div></div>
          <ul class="attacks"><li>Bullet Punch</li><li>Close Combat</li><li>Swords Dance</li><li>Protect</li></ul>
        </div>
      </div>
    </div>
  </body>
</html>
"""

LIMITLESS_TEAMLIST_HTML_2 = """
<html>
  <body>
    <div class="teamlist">
      <div class="teamlist-pokemon">
        <div class="pkmn">
          <div class="name"><span>Charizard</span></div>
          <div class="details"><div class="item">Charizardite Y</div><div class="ability">Ability: Blaze</div></div>
          <ul class="attacks"><li>Heat Wave</li><li>Solar Beam</li><li>Protect</li><li>Air Slash</li></ul>
        </div>
        <div class="pkmn">
          <div class="name"><span>Garchomp</span></div>
          <div class="details"><div class="item">Choice Scarf</div><div class="ability">Ability: Rough Skin</div></div>
          <ul class="attacks"><li>Earthquake</li><li>Rock Slide</li><li>Dragon Claw</li><li>Protect</li></ul>
        </div>
        <div class="pkmn">
          <div class="name"><span>Basculegion</span></div>
          <div class="details"><div class="item">Focus Sash</div><div class="ability">Ability: Adaptability</div></div>
          <ul class="attacks"><li>Wave Crash</li><li>Last Respects</li><li>Aqua Jet</li><li>Protect</li></ul>
        </div>
        <div class="pkmn">
          <div class="name"><span>Kingambit</span></div>
          <div class="details"><div class="item">Chople Berry</div><div class="ability">Ability: Defiant</div></div>
          <ul class="attacks"><li>Sucker Punch</li><li>Kowtow Cleave</li><li>Iron Head</li><li>Protect</li></ul>
        </div>
        <div class="pkmn">
          <div class="name"><span>Sylveon</span></div>
          <div class="details"><div class="item">Fairy Feather</div><div class="ability">Ability: Pixilate</div></div>
          <ul class="attacks"><li>Hyper Voice</li><li>Quick Attack</li><li>Protect</li><li>Helping Hand</li></ul>
        </div>
        <div class="pkmn">
          <div class="name"><span>Aerodactyl</span></div>
          <div class="details"><div class="item">Aerodactylite</div><div class="ability">Ability: Unnerve</div></div>
          <ul class="attacks"><li>Rock Slide</li><li>Tailwind</li><li>Dual Wingbeat</li><li>Protect</li></ul>
        </div>
      </div>
    </div>
  </body>
</html>
"""

LIMITLESS_STANDINGS_HTML_1 = """
<html>
  <body>
    <table class="striped">
      <tr>
        <th>Place</th>
        <th>Name</th>
        <th></th>
        <th>Points</th>
        <th>Record</th>
      </tr>
      <tr>
        <td>1</td>
        <td>Altkyle</td>
        <td></td>
        <td>12</td>
        <td>12 - 0 - 0</td>
      </tr>
      <tr>
        <td>2</td>
        <td>Kojay</td>
        <td></td>
        <td>9</td>
        <td>9 - 3 - 0</td>
      </tr>
      <tr>
        <td>3</td>
        <td>charismaacheck</td>
        <td></td>
        <td>9</td>
        <td>9 - 2 - 0</td>
      </tr>
    </table>
  </body>
</html>
"""

LIMITLESS_STANDINGS_HTML_2 = """
<html>
  <body>
    <table class="striped">
      <tr>
        <th>Place</th>
        <th>Name</th>
        <th></th>
        <th>Points</th>
        <th>Record</th>
      </tr>
      <tr>
        <td>1</td>
        <td>Ksomon</td>
        <td></td>
        <td>10</td>
        <td>10 - 0 - 0</td>
      </tr>
      <tr>
        <td>2</td>
        <td>Second Place</td>
        <td></td>
        <td>8</td>
        <td>8 - 2 - 0</td>
      </tr>
      <tr>
        <td>3</td>
        <td>Third Place</td>
        <td></td>
        <td>7</td>
        <td>7 - 2 - 0</td>
      </tr>
      <tr>
        <td>4</td>
        <td>Fourth Place</td>
        <td></td>
        <td>6</td>
        <td>6 - 3 - 0</td>
      </tr>
    </table>
  </body>
</html>
"""

LIMITLESS_DETAILS_HTML_1 = """
<html>
  <body>
    <table>
      <tr><td>Organized by</td><td>HM4coach & The Boys</td></tr>
      <tr><td></td><td>Wednesday, May 13, 2026</td></tr>
      <tr><td></td><td>06:00 PM EDT</td></tr>
    </table>
  </body>
</html>
"""

LIMITLESS_DETAILS_HTML_2 = """
<html>
  <body>
    <table>
      <tr><td>Organized by</td><td>SpearPillar</td></tr>
      <tr><td></td><td>Tuesday, May 20, 2026</td></tr>
      <tr><td></td><td>08:00 PM EDT</td></tr>
    </table>
  </body>
</html>
"""

LIMITLESS_DETAILS_HTML_3 = """
<html>
  <head>
    <meta name="description" content="May 13, 2026 - Regulation Set M-A format - HM4coach &amp; The Boys">
  </head>
  <body></body>
</html>
"""

SNAPSHOT = {
    "snapshot_date": "2026-05-20",
    "snapshot_at_utc": "2026-05-20T13:55:48Z",
    "source": "pikalytics",
    "format_slug": "gen9championsvgc2026regma",
    "format_name": "Pokemon Champions VGC 2026 Regulation Set M-A",
    "source_url": "https://pikalytics.com/pokedex/gen9championsvgc2026regma",
}


format_soup = BeautifulSoup(FORMAT_SAMPLE_HTML, "html.parser")
top_teams_soup = BeautifulSoup(TOP_TEAMS_SAMPLE_HTML, "html.parser")

section("parse_pokemon_usage")
usage_rows = parse_pokemon_usage(format_soup, SNAPSHOT)
check("usage row count", len(usage_rows), 2)
check("first usage Pokemon", usage_rows[0]["pokemon_name"], "Sneasler")
check("first usage percent", usage_rows[0]["usage_pct"], "43.80")

section("parse_team_cores")
core_rows = parse_team_cores(format_soup, SNAPSHOT)
check("core row count", len(core_rows), 1)
check("core size", core_rows[0]["core_size"], "2")
check("core team count", core_rows[0]["team_count"], "521")
check("core Pokemon list", core_rows[0]["pokemon_names"], "Charizard-Mega-Y:Garchomp")

section("parse_recent_top_teams")
team_rows = parse_recent_top_teams(top_teams_soup, SNAPSHOT)
check("team row count", len(team_rows), 2)
check("team author", team_rows[0]["author"], "Altkyle")
check("team source URL", team_rows[0]["team_source_url"], "https://example.com/team-1")
check("team uses collage forms", team_rows[1]["pokemon_names"], "Charizard-Mega-Y:Garchomp:Basculegion:Kingambit:Sylveon:Aerodactyl-Mega")
check("team parses wins", team_rows[0]["wins"], "12")
check("team parses event rank", team_rows[0]["event_rank"], "1")
check("team marks event winner", team_rows[0]["won_event"], "1")

section("parse_team_source_details_html")
source_team_1 = parse_team_source_details_html(LIMITLESS_TEAMLIST_HTML_1)
source_team_2 = parse_team_source_details_html(LIMITLESS_TEAMLIST_HTML_2)
check("source team 1 row count", len(source_team_1), 3)
check("source team 1 first item", source_team_1[0]["item"], "Dragoninite")
check("source team 1 first ability", source_team_1[0]["ability"], "Inner Focus")
check("source team 2 last move", source_team_2[5]["move_4"], "Protect")

section("parse_event_standings_html")
standings_1 = parse_event_standings_html(LIMITLESS_STANDINGS_HTML_1)
standings_2 = parse_event_standings_html(LIMITLESS_STANDINGS_HTML_2)
check("standings player count 1", standings_1["event_player_count"], "3")
check("standings player count 2", standings_2["event_player_count"], "4")

section("parse_event_details_html")
details_1 = parse_event_details_html(LIMITLESS_DETAILS_HTML_1)
details_2 = parse_event_details_html(LIMITLESS_DETAILS_HTML_2)
details_3 = parse_event_details_html(LIMITLESS_DETAILS_HTML_3)
check("event date 1", details_1["event_date"], "2026-05-13")
check("event date 2", details_2["event_date"], "2026-05-20")
check("event date 3", details_3["event_date"], "2026-05-13")

section("enrich_top_teams_with_source_details")
source_detail_map = {
  "https://play.limitlesstcg.com/tournament/example-1/player/altkyle/teamlist": source_team_1,
  "https://play.limitlesstcg.com/tournament/example-2/player/ksomon/teamlist": source_team_2,
}
event_metadata_map = {
  "https://play.limitlesstcg.com/tournament/example-1/player/altkyle/teamlist": {
      **details_1,
      **standings_1,
      "event_standings_url": "https://play.limitlesstcg.com/tournament/example-1/standings",
  },
  "https://play.limitlesstcg.com/tournament/example-2/player/ksomon/teamlist": {
      **details_2,
      **standings_2,
      "event_standings_url": "https://play.limitlesstcg.com/tournament/example-2/standings",
  },
}
team_rows[0]["team_source_url"] = "https://play.limitlesstcg.com/tournament/example-1/player/altkyle/teamlist"
team_rows[1]["team_source_url"] = "https://play.limitlesstcg.com/tournament/example-2/player/ksomon/teamlist"
enriched_team_rows, team_pokemon_detail_rows = enrich_top_teams_with_source_details(
    team_rows,
    source_detail_loader=lambda url: source_detail_map.get(url, []),
    event_metadata_loader=lambda url: event_metadata_map.get(url, {"event_date": "", "event_player_count": "", "event_standings_url": ""}),
)
scizor_row = next(row for row in team_pokemon_detail_rows if row["pokemon_name"] == "Scizor-Mega")
charizard_team_row = next(row for row in enriched_team_rows if row["author"] == "Ksomon")
check("team details row count", len(team_pokemon_detail_rows), 9)
check("enriched top team item", charizard_team_row["pokemon_1_item"], "Charizardite Y")
check("enriched top team moves", charizard_team_row["pokemon_2_moves"], "Earthquake:Rock Slide:Dragon Claw:Protect")
check("enriched top team date", charizard_team_row["event_date"], "2026-05-20")
check("enriched top team player count", charizard_team_row["event_player_count"], "4")
check("enriched top team standings URL", charizard_team_row["event_standings_url"], "https://play.limitlesstcg.com/tournament/example-2/standings")
check("normalized team detail keeps pikalytics form name", scizor_row["pokemon_name"], "Scizor-Mega")
check("normalized team detail stores source name", scizor_row["source_pokemon_name"], "Scizor")
check("normalized team detail carries event date", scizor_row["event_date"], "2026-05-13")
check("normalized team detail carries player count", scizor_row["event_player_count"], "3")
check("normalized team detail stores teammates", scizor_row["teammates"], "Dragonite:Basculegion")

section("build_team_combination_summaries")
combo_rows = build_team_combination_summaries(enriched_team_rows, SNAPSHOT)
charizard_combo_row = next(row for row in combo_rows if row["pokemon_names"] == "Charizard-Mega-Y")
pair_row = next(row for row in combo_rows if row["pokemon_names"] == "Basculegion:Garchomp")
check("single Pokemon appearances aggregate", charizard_combo_row["team_appearances"], "1")
check("single Pokemon total wins aggregate", charizard_combo_row["wins"], "10")
check("pairing type label", pair_row["combination_type"], "pairing")
check("pairing appearances aggregate", pair_row["team_appearances"], "1")
check("pairing matches aggregate", pair_row["matches_played"], "10")

section("build_pokemon_team_metrics")
pokemon_team_rows = build_pokemon_team_metrics(combo_rows, usage_rows, SNAPSHOT)
garchomp_team_metric = next(row for row in pokemon_team_rows if row["pokemon_name"] == "Garchomp")
charizard_team_metric = next(row for row in pokemon_team_rows if row["pokemon_name"] == "Charizard-Mega-Y")
check("pokemon team metrics row count", len(pokemon_team_rows), 8)
check("usage rank is joined onto Pokemon team metrics", garchomp_team_metric["usage_rank"], "2")
check("usage pct is joined onto Pokemon team metrics", garchomp_team_metric["usage_pct"], "40.40")
check("missing source usage remains blank", charizard_team_metric["usage_rank"], "")
check("Pokemon team metrics aggregate appearances", charizard_team_metric["team_appearances"], "1")
check("Pokemon team metrics top teammates", garchomp_team_metric["top_teammates"], "Aerodactyl-Mega:Basculegion:Charizard-Mega-Y:Kingambit:Sylveon")

section("merge_dataset_rows")
existing_top_team_row = {
  **enriched_team_rows[0],
  "snapshot_date": "2026-05-20",
  "snapshot_at_utc": "2026-05-20T10:00:00Z",
  "event_player_count": "",
}
rerun_top_team_row = {
  **enriched_team_rows[0],
  "snapshot_date": "2026-05-21",
  "snapshot_at_utc": "2026-05-21T11:00:00Z",
  "event_player_count": "3",
}
merged_top_team_rows, top_inserted, top_updated = merge_dataset_rows(
    "top_teams",
  [existing_top_team_row],
    [rerun_top_team_row],
)
check("top teams rerun keeps one tournament row", len(merged_top_team_rows), 1)
check("top teams rerun updates row", top_updated, 1)
check("top teams rerun inserts no duplicate", top_inserted, 0)
check("top teams rerun keeps latest player count", merged_top_team_rows[0]["event_player_count"], "3")
check("top teams rerun keeps latest snapshot date", merged_top_team_rows[0]["snapshot_date"], "2026-05-21")

legacy_duplicate_row = {
  **team_pokemon_detail_rows[0],
  "snapshot_at_utc": "2026-05-20T10:00:00Z",
  "team_source_url": "",
  "item": "",
}
merged_rows, inserted, updated = merge_dataset_rows(
    "team_pokemon_details",
  team_pokemon_detail_rows + [legacy_duplicate_row],
    [{**team_pokemon_detail_rows[0], "item": "Updated Item"}],
)
check("merge keeps row count", len(merged_rows), 9)
check("merge updates existing row", updated, 1)
check("merge inserts no duplicate", inserted, 0)
check("merge overwrites matching row", merged_rows[0]["item"], "Updated Item")
check("merge drops legacy duplicate row", sum(1 for row in merged_rows if row["pokemon_name"] == "Dragonite"), 1)

passed = sum(1 for result in _results if result)
total = len(_results)
print(f"\n{'═' * 60}")
if passed == total:
    print(f"  Results: {passed}/{total} passed  ✓ all clear")
else:
    print(f"  Results: {passed}/{total} passed  ✗ failures present")
print(f"{'═' * 60}")

raise SystemExit(0 if passed == total else 1)