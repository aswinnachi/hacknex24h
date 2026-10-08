import urllib.request
import json
import time

def run_test_suite():
    base_url = "http://127.0.0.1:8000/api/query"
    camera_id = "camera_01"

    test_queries = [
        # Part 30 - Object Counts
        {"category": "OBJECT COUNT", "query": "how many cars", "expected_type": "COUNT"},
        {"category": "OBJECT COUNT", "query": "how many buses", "expected_type": "COUNT"},
        {"category": "OBJECT COUNT", "query": "how many bicycles", "expected_type": "COUNT"},
        {"category": "OBJECT COUNT", "query": "how many people", "expected_type": "COUNT"},

        # Part 30 - Attributes
        {"category": "ATTRIBUTES", "query": "show a person wearing a white shirt", "expected_type": "SEARCH"},
        {"category": "ATTRIBUTES", "query": "show a person wearing black pants", "expected_type": "SEARCH"},
        {"category": "ATTRIBUTES", "query": "show a red car", "expected_type": "SEARCH"},
        {"category": "ATTRIBUTES", "query": "show a moving red car", "expected_type": "SEARCH"},

        # Part 30 - Relationships
        {"category": "RELATIONSHIPS", "query": "show a person carrying a backpack", "expected_type": "SEARCH"},
        {"category": "RELATIONSHIPS", "query": "show a person carrying a bag", "expected_type": "SEARCH"},
        {"category": "RELATIONSHIPS", "query": "show a person holding a phone", "expected_type": "SEARCH"},
        {"category": "RELATIONSHIPS", "query": "show a person with an umbrella", "expected_type": "SEARCH"},

        # Part 30 - Negative
        {"category": "NEGATIVE", "query": "show a person without a bag", "expected_type": "SEARCH"},

        # Part 30 - Actions
        {"category": "ACTIONS", "query": "show a walking person", "expected_type": "SEARCH"},
        {"category": "ACTIONS", "query": "show a moving car", "expected_type": "SEARCH"},

        # Part 30 - Combined
        {"category": "COMBINED", "query": "show a person wearing a white shirt carrying a black backpack", "expected_type": "SEARCH"},

        # Part 30 - Count + Attribute
        {"category": "COUNT + ATTR", "query": "how many people are wearing white shirts?", "expected_type": "COUNT"},
        {"category": "COUNT + ATTR", "query": "how many red cars are moving?", "expected_type": "COUNT"},
    ]

    print("=" * 80)
    print("RUNNING MASTER TEST SUITE FOR MULTI-STREAM VIDEO INTELLIGENCE")
    print("=" * 80)

    results_summary = []

    for test in test_queries:
        payload = {
            "query": test["query"],
            "camera_id": camera_id
        }
        req = urllib.request.Request(
            base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                
                verdict = res_data.get("verdict", "")
                summary = res_data.get("summary", "")
                results = res_data.get("results", [])
                debug = res_data.get("debug_info", {})
                
                event_count = len(results)
                top_ts = f"{results[0]['timestamp']:.2f}s" if results else "N/A"
                top_track = f"#{results[0]['tracking_id']}" if results else "N/A"

                print(f"\n[{test['category']}] Query: '{test['query']}'")
                print(f"  → Verdict: {verdict}")
                print(f"  → Matches: {event_count} (Top: {top_track} @ {top_ts})")
                print(f"  → Summary: {summary}")
                if debug:
                    parsed_intent = debug.get("parsed_intent", {})
                    print(f"  → Parsed: intent={parsed_intent.get('intent')} obj={parsed_intent.get('object_class')} color={parsed_intent.get('color')}")
                
                results_summary.append({
                    "query": test["query"],
                    "category": test["category"],
                    "verdict": verdict,
                    "summary": summary,
                    "top_track": top_track,
                    "top_ts": top_ts
                })
        except Exception as e:
            print(f"\n[{test['category']}] Query: '{test['query']}' FAILED with error: {e}")
            results_summary.append({
                "query": test["query"],
                "category": test["category"],
                "status": f"ERROR: {e}",
                "summary": str(e),
                "top_track": "N/A",
                "top_ts": "N/A"
            })

    print("\n" + "=" * 80)
    print("MASTER TEST SUITE COMPLETED")
    print("=" * 80)

if __name__ == "__main__":
    time.sleep(1)
    run_test_suite()
