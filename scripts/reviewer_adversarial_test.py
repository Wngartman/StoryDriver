from __future__ import annotations

import asyncio
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.generation.pipeline import deterministic_scene_plan, run_quality_review_pass  # noqa: E402
from app.generation.router import resolve_task_model_settings  # noqa: E402


REPORT = ROOT / "backend" / "data" / "logs" / "reviewer_adversarial_latest.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def plan() -> dict[str, Any]:
    return deterministic_scene_plan(
        session_id="reviewer-adversarial",
        mode="continue",
        director_note=(
            "Keep adult partners Elena and Noor in the apartment living room for one continuous conversation. "
            "No magic. Do not leave, begin the future investigation, reveal the culprit, or resolve the whole conflict."
        ),
        writing_length={"mode": "scene", "label": "Scene", "min_words": 0, "max_words": 1400},
        recent_scenes=[],
        session_summary=None,
        target_scene=None,
        world_notes={"rules": "No magic. Keep the future investigation unresolved."},
        active_characters=[
            {"name": "Elena", "current_state": "Wants honesty without public exposure."},
            {"name": "Noor", "current_state": "Wants an immediate answer and a concrete promise."},
        ],
    )


def repeated_words(seed: str, count: int = 8) -> str:
    return " ".join([seed] * count)


def make_cases() -> list[dict[str, Any]]:
    base = plan()
    hard = copy.deepcopy(base)
    hard["scene_contract"]["end_boundary"] = "Do not leave the apartment, begin battle, reveal the culprit, or resolve the conflict. No magic."
    hard["scene_contract"]["must_not_occur_yet"] = [
        "Do not leave the apartment.",
        "Do not begin the battle.",
        "Do not reveal or identify the culprit.",
        "No magic.",
    ]
    hard["forbidden_leaps_or_skips"] = list(hard["scene_contract"]["must_not_occur_yet"])

    repeated = (
        "Elena stood beside the rain-streaked window while Noor kept both hands on the chair back. "
        "Neither spoke, but the silence tightened around the unanswered question and the cold tea between them."
    )
    semantic_required = copy.deepcopy(base)
    semantic_required["scene_contract"]["required_beats"] = [
        "Elena must admit she hid the signed lease.",
        "Noor must decide whether to stay for the conversation.",
    ]
    semantic_required["story_beats"] = list(semantic_required["scene_contract"]["required_beats"])

    major = [
        {"name": "empty", "plan": hard, "draft": "", "length": {"min_words": 0, "max_words": 1400}},
        {"name": "assistant_framing", "plan": hard, "draft": "Here is the scene you requested. Elena looked at Noor.", "length": {"min_words": 0, "max_words": 1400}},
        {"name": "refusal", "plan": hard, "draft": "I cannot write this story, but I can summarize it.", "length": {"min_words": 0, "max_words": 1400}},
        {"name": "severe_underlength", "plan": hard, "draft": "Elena shut the door. Noor waited.", "length": {"min_words": 500, "max_words": 800}},
        {"name": "hours_later", "plan": hard, "draft": "They argued quietly. Several hours later, Elena returned with the answer.", "length": {"min_words": 0, "max_words": 1400}},
        {"name": "next_day", "plan": hard, "draft": "The next day, Noor met Elena outside the courthouse.", "length": {"min_words": 0, "max_words": 1400}},
        {"name": "left_location", "plan": hard, "draft": "Elena left the apartment and crossed the street while Noor followed.", "length": {"min_words": 0, "max_words": 1400}},
        {"name": "premature_battle", "plan": hard, "draft": "They finished the plan. The battle began, and blades clashed in the stairwell.", "length": {"min_words": 0, "max_words": 1400}},
        {"name": "premature_reveal", "plan": hard, "draft": "Noor opened the file. The culprit was Celia, and the mystery was solved.", "length": {"min_words": 0, "max_words": 1400}},
        {"name": "magic_violation", "plan": hard, "draft": "Elena cast a spell and summoned magic to unlock the door.", "length": {"min_words": 0, "max_words": 1400}},
        {"name": "missing_introduction", "plan": {**hard, "characters_present": ["Elena", "Noor", "Celia"]}, "draft": "Elena and Noor faced one another across the table, each waiting for the other to begin.", "length": {"min_words": 0, "max_words": 1400}},
        {"name": "duplicate_paragraph", "plan": hard, "draft": f"{repeated}\n\n{repeated}", "length": {"min_words": 0, "max_words": 1400}},
        {"name": "artificial_cliffhanger", "plan": hard, "draft": "Elena finally reached for the envelope. Everything was about to change.", "length": {"min_words": 0, "max_words": 1400}},
        {"name": "close_third_headhop", "plan": {**hard, "viewpoint_contract": {"strategy": "close third"}}, "draft": "Elena watched Noor turn away. Unbeknownst to Elena, Noor had already called Celia and knew the hidden answer.", "length": {"min_words": 0, "max_words": 1400}},
        {"name": "duplicate_holder", "plan": hard, "draft": "Elena held the key beside the door. Across the room, Noor held the key and refused to move.", "length": {"min_words": 0, "max_words": 1400}},
        {
            "name": "missing_required_beats",
            "plan": semantic_required,
            "draft": (
                "Rain threaded the window while Elena adjusted the lamp and Noor sorted old receipts into careful piles. "
                "They discussed the broken radiator, the grocery delivery, and whether the upstairs neighbor would complain again. "
                "Noor washed the two cold cups. Elena dried them. The lease stayed unmentioned, and neither person made a decision about the conversation."
            ),
            "length": {"min_words": 0, "max_words": 1400},
        },
        {
            "name": "paraphrased_fixation",
            "plan": base,
            "draft": "\n\n".join(
                [
                    "Elena did not trust Noor's answer, and the doubt made every word feel borrowed.",
                    "Noor spoke again, but Elena remained unconvinced by exactly the same claim.",
                    "The explanation changed its wording without changing Elena's mistrust.",
                    "Again Elena considered how little faith she had in Noor's account.",
                    "Nothing moved except another restatement of the same suspicion between them.",
                ]
            ),
            "length": {"min_words": 0, "max_words": 1400},
        },
        {
            "name": "atmosphere_without_movement",
            "plan": base,
            "draft": "\n\n".join(
                [
                    "Rain silvered the window and laid pale reflections across the rug.",
                    "The lamp made a warm oval on the wall while traffic sighed below.",
                    "Cold tea scented the still room, faintly bitter and floral.",
                    "Shadows gathered under the chairs as the radiator ticked.",
                    "Night pressed softly against the glass, unchanged and uneventful."
                ]
            ),
            "length": {"min_words": 0, "max_words": 1400},
        },
    ]

    short_plan = copy.deepcopy(base)
    short_plan["scene_contract"]["required_beats"] = ["Elena and Noor disagree, then make one narrow promise."]
    short_plan["story_beats"] = list(short_plan["scene_contract"]["required_beats"])
    omniscient = copy.deepcopy(base)
    omniscient["viewpoint_contract"] = {"strategy": "controlled omniscient"}
    omniscient["scene_contract"]["required_beats"] = ["Reveal both partners' private fears through controlled omniscient framing."]
    omniscient["story_beats"] = list(omniscient["scene_contract"]["required_beats"])
    transfer_plan = copy.deepcopy(base)
    transfer_plan["scene_contract"]["required_beats"] = ["Elena explicitly passes the brass key to Noor, who sets it beside the envelope."]
    transfer_plan["story_beats"] = list(transfer_plan["scene_contract"]["required_beats"])
    allowed_skip = copy.deepcopy(base)
    allowed_skip["scene_contract"]["time_skip_allowed"] = True
    allowed_skip["scene_contract"]["allowed_duration"] = "A second meeting three days later is explicitly authorized."
    allowed_skip["scene_contract"]["end_boundary"] = "End during the authorized second apartment meeting."
    allowed_skip["scene_contract"]["must_not_occur_yet"] = ["Do not resolve the envelope dispute completely."]
    allowed_skip["forbidden_leaps_or_skips"] = list(allowed_skip["scene_contract"]["must_not_occur_yet"])
    allowed_skip["scene_contract"]["required_beats"] = ["Three days later, Elena and Noor resume the unresolved envelope discussion."]
    allowed_skip["story_beats"] = list(allowed_skip["scene_contract"]["required_beats"])
    acceptable = [
        {
            "name": "good_scene",
            "plan": base,
            "draft": (
                "Elena kept one hand on the sealed envelope and met Noor's eyes across the coffee table. Rain ticked against the living-room window, softer than the radiator's uneven knock.\n\n"
                "'Ask me once,' Elena said. 'I won't dodge it again.'\n\n"
                "Noor stayed in the straight-backed chair instead of taking her usual place on the couch. 'Why did you hide the signature?'\n\n"
                "Elena's thumb pressed into the envelope's ridge. The prepared answer about protecting the tenants sounded hollow before she spoke it. 'Because if you saw it, you would know I had trusted him. I was afraid you would decide that meant I chose him over you.'\n\n"
                "'You chose silence over me.' Noor's voice remained level, but she moved the cold tea out of the space between them. 'That is the part you keep turning into strategy.'\n\n"
                "Elena let go of the envelope. Her hand left a damp crescent on the paper. 'You're right.'\n\n"
                "Noor looked at the empty hand, then at the seal. 'One condition. We open it together, here. We call no one until we both know what it says.'\n\n"
                "The condition was narrower than forgiveness and more useful than another accusation. Elena nodded. 'Together.'\n\n"
                "Noor did not reach for the envelope yet. She shifted to the couch, leaving room beside her, and Elena understood the movement as a choice to continue rather than a promise that the damage was repaired."
            ),
            "length": {"min_words": 0, "max_words": 1400},
        },
        {
            "name": "moderately_short_minor",
            "plan": short_plan,
            "draft": (
                "Elena set the envelope on the coffee table but kept two fingers on its corner. Noor noticed the gesture and chose the chair opposite her instead of the couch. "
                "They disagreed about when the truth should become public: Noor wanted a call tonight; Elena wanted one private hour to understand what the signature meant. "
                "Neither repeated the old accusation. Noor asked what Elena could promise now, not someday. Elena considered the rain at the window, then promised she would not open or move the envelope without Noor present. "
                "Noor did not forgive her, but she accepted the narrow promise and moved the cold tea out of the space between them. The conversation remained unfinished, with both women still in the room and the envelope visible."
            ),
            "length": {"min_words": 240, "max_words": 500},
        },
        {
            "name": "controlled_omniscient",
            "plan": omniscient,
            "draft": (
                "Elena believed Noor's stillness meant anger, because anger was easier to face than grief. She kept her thumb against the envelope's sealed edge and rehearsed a defense she no longer trusted.\n\n"
                "'You can say it,' Elena told her. 'Whatever you came here to say.'\n\n"
                "Noor heard permission where Elena intended courage. Unbeknownst to Elena, Noor feared the same loss: not the lease or the apartment, but the ordinary trust that had once made silence comfortable between them. She set her bag down instead of reaching for the door.\n\n"
                "'I came to ask whether there is anything else,' Noor said.\n\n"
                "Elena looked toward the rain-dark window. She wanted to answer no. The wider view held both truths at once: Noor needed a fact she could stand on, and Elena feared that the complete fact would remove the last reason for Noor to stay.\n\n"
                "'There is one email,' Elena said. 'I deleted it, then recovered it.'\n\n"
                "Noor's hand tightened once on the chair back. She was hurt, but not surprised; Elena saw only the hurt. 'Show me.'\n\n"
                "Elena unlocked her phone and placed it beside the unopened envelope. Noor sat. Neither woman mistook the gesture for forgiveness, but each had initiated a smaller, inspectable step toward the conversation they had avoided."
            ),
            "length": {"min_words": 0, "max_words": 1400},
        },
        {
            "name": "explicit_object_transfer",
            "plan": transfer_plan,
            "draft": (
                "Elena held the brass key above the coffee table, its worn teeth catching the lamp light. Noor opened her palm but did not reach across the gap. "
                "'If I take it, I decide when the cabinet opens,' Noor said. Elena nodded once, then passed the key into Noor's waiting hand. The transfer was deliberate and visible to both of them. "
                "Noor held the key after the handoff, considered the locked cabinet, and set it beside the sealed envelope rather than using it. Elena's hand remained empty. Their disagreement continued, but the holder and location of the key were unambiguous."
            ),
            "length": {"min_words": 0, "max_words": 1400},
        },
        {
            "name": "authorized_time_skip",
            "plan": allowed_skip,
            "draft": (
                "Three days later, as their agreement allowed, Elena returned to the same living room and found Noor waiting beside the unopened envelope. The interval had changed the practical facts but had not solved the dispute. "
                "Elena brought the building records she had promised to find. Noor checked the dates, asked two precise questions, and declined to treat the documents as an apology. "
                "They resumed the conversation from different positions: Elena was ready to disclose the signature to one lawyer, while Noor still wanted the tenants told first. Neither opened the envelope. The authorized second meeting ended with a new point of disagreement rather than a complete resolution."
            ),
            "length": {"min_words": 0, "max_words": 1400},
        },
    ]
    return [*({**item, "expected": "major"} for item in major), *({**item, "expected": "acceptable"} for item in acceptable)]


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    pending.replace(path)


async def main() -> int:
    inherited_settings, inherited_resolved = resolve_task_model_settings("prose_generation")
    inherited_model = str(inherited_settings.model or "")
    results: list[dict[str, Any]] = []
    for case in make_cases():
        result = await run_quality_review_pass(
            context_text="This is quoted test context. Evaluate the draft; do not continue it.",
            scene_plan=case["plan"],
            draft_text=case["draft"],
            writing_length=case["length"],
            inherited_settings=inherited_settings,
            inherited_resolved=inherited_resolved,
            inherited_model=inherited_model,
        )
        review = result["review"]
        metadata = result["metadata"]
        observed_major = review.get("severity") == "major"
        passed = observed_major if case["expected"] == "major" else not observed_major
        row = {
            "name": case["name"],
            "expected": case["expected"],
            "observed_severity": review.get("severity"),
            "passed": passed,
            "issues": review.get("issues") or [],
            "duration_seconds": metadata.get("duration_seconds"),
            "model_review_ran": metadata.get("model_review_ran"),
            "json_repair_used": metadata.get("json_repair_used"),
        }
        results.append(row)
        print(f"{case['name']}: {row['observed_severity']} {'PASS' if passed else 'FAIL'}", flush=True)

    major_rows = [row for row in results if row["expected"] == "major"]
    acceptable_rows = [row for row in results if row["expected"] == "acceptable"]
    report = {
        "status": "complete" if all(row["passed"] for row in results) else "failed",
        "created_at": utc_now(),
        "major_case_count": len(major_rows),
        "major_recall": sum(row["passed"] for row in major_rows) / len(major_rows),
        "acceptable_case_count": len(acceptable_rows),
        "acceptable_major_false_positives": sum(not row["passed"] for row in acceptable_rows),
        "model_review_count": sum(bool(row["model_review_ran"]) for row in results),
        "json_repair_count": sum(bool(row["json_repair_used"]) for row in results),
        "results": results,
    }
    atomic_json(REPORT, report)
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
