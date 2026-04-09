import asyncio
from models.schemas import PlanRequest
from agents.orchestrator import run_orchestrator

async def main():
    req = PlanRequest(
        user_id="test_user",
        input_text="Write 10-page research paper (high, hard, spread:12), Build new website prototype (medium, hard, spread:10), Personal fitness challenge (low, medium, spread:10)",
        current_plan=[],
        current_unscheduled=[]
    )
    try:
        print("Running orchestrator...")
        res = await run_orchestrator(req)
        print("✅ Success! Plan generated.")
    except Exception as e:
        import traceback
        print("\n❌ CRASH DETECTED. Here is the real error:\n")
        traceback.print_exc()

asyncio.run(main())
