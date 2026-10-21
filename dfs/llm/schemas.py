"""JSON schemas the agent stages must answer in (enforced by the SDK's structured output)."""

from __future__ import annotations

_FACT = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "short unique id, e.g. F3"},
        "subject": {"type": "string", "description": "player full name, team abbreviation, or 'game'"},
        "team": {"type": "string"},
        "kind": {"type": "string", "enum": ["injury", "status", "role", "usage", "depth_chart", "matchup",
                                             "weather", "narrative", "line", "coaching", "other"]},
        "text": {"type": "string"},
        "source_url": {"type": "string"},
        "as_of": {"type": "string", "description": "date/time the source states, ISO if possible"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["id", "subject", "kind", "text", "confidence"],
}

RESEARCH = {
    "type": "object",
    "properties": {
        "game": {"type": "string"},
        "summary": {"type": "string", "description": "3-6 sentences: what decides fantasy outcomes in this game"},
        "facts": {"type": "array", "items": _FACT},
        "ruled_out": {"type": "array", "items": {"type": "string"}, "description": "players confirmed OUT/inactive/IR"},
        "doubtful_or_questionable": {"type": "array", "items": {"type": "string"}},
        "weather": {"type": "string"},
        "searches_used": {"type": "integer"},
    },
    "required": ["game", "summary", "facts", "ruled_out", "doubtful_or_questionable"],
}

FORECAST = {
    "type": "object",
    "properties": {
        "game": {"type": "string"},
        "players": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "player_key": {"type": "string"},
                    "low": {"type": "number", "description": "10th percentile fantasy points if active"},
                    "likely": {"type": "number", "description": "median fantasy points if active"},
                    "high": {"type": "number", "description": "90th percentile fantasy points if active"},
                    "p_dnp": {"type": "number", "description": "probability of not playing (0-1)"},
                    "stat_line": {"type": "string", "description": "median stat line behind 'likely', e.g. '6 rec, 64 yds, 0.4 TD'"},
                    "rationale": {"type": "string", "description": "one sentence citing fact ids"},
                },
                "required": ["player_key", "low", "likely", "high", "p_dnp", "rationale"],
            },
        },
    },
    "required": ["game", "players"],
}

STRATEGIST = {
    "type": "object",
    "properties": {
        "remove_not_playing": {
            "type": "array",
            "items": {"type": "object", "properties": {"player_key": {"type": "string"},
                                                       "fact_ids": {"type": "array", "items": {"type": "string"}}},
                      "required": ["player_key", "fact_ids"]},
        },
        "cuts": {
            "type": "array",
            "description": "picks to cut; each MUST cite fact ids that make the pick wrong",
            "items": {"type": "object", "properties": {"player_key": {"type": "string"},
                                                       "fact_ids": {"type": "array", "items": {"type": "string"}},
                                                       "reason": {"type": "string"}},
                      "required": ["player_key", "fact_ids", "reason"]},
        },
        "close_calls": {"type": "array", "items": {"type": "string"}},
        "weakest_pick": {"type": "object", "properties": {"player_key": {"type": "string"}, "why": {"type": "string"}},
                         "required": ["player_key", "why"]},
        "preferred_draft": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["remove_not_playing", "cuts", "close_calls", "weakest_pick", "preferred_draft"],
}

CHECKER = {
    "type": "object",
    "properties": {
        "attacks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "player_key": {"type": "string"},
                    "claim": {"type": "string", "enum": ["not_playing", "limited_or_role_cut", "bad_matchup",
                                                         "better_alternative", "correlation_problem", "no_issue"]},
                    "severity": {"type": "string", "enum": ["blocker", "major", "minor", "none"]},
                    "fact_ids": {"type": "array", "items": {"type": "string"}},
                    "argument": {"type": "string"},
                },
                "required": ["player_key", "claim", "severity", "fact_ids", "argument"],
            },
        },
    },
    "required": ["attacks"],
}

BACKUP = {
    "type": "object",
    "properties": {
        "adjustments": {
            "type": "array",
            "items": {"type": "object", "properties": {"player_key": {"type": "string"},
                                                       "multiplier": {"type": "number", "description": "0.5-1.3 on the mean"},
                                                       "fact_ids": {"type": "array", "items": {"type": "string"}},
                                                       "reason": {"type": "string"}},
                      "required": ["player_key", "multiplier", "fact_ids", "reason"]},
        },
        "remove": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": ["adjustments", "remove"],
}

NEWS = {
    "type": "object",
    "properties": {
        "updates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "player": {"type": "string"},
                    "team": {"type": "string"},
                    "status": {"type": "string", "enum": ["active", "inactive", "out", "questionable", "game_time_decision",
                                                          "limited_role", "expanded_role", "no_news"]},
                    "text": {"type": "string"},
                    "source_url": {"type": "string"},
                    "as_of": {"type": "string"},
                },
                "required": ["player", "status", "text"],
            },
        },
    },
    "required": ["updates"],
}
