import json
from typing import Any
from models.schemas import ScheduledDay


class MCPServer:
    def __init__(self):
        self.tools = {
            "google_calendar": self._calendar_tool,
            "task_manager": self._task_manager_tool,
            "daily_summary": self._summary_tool,
            "productivity_score": self._productivity_tool,
        }

    async def call_tool(self, tool_name: str, params: dict) -> Any:
        if tool_name not in self.tools:
            return {"error": f"Tool {tool_name} not found"}
        try:
            return await self.tools[tool_name](params)
        except Exception as e:
            return {"error": str(e), "tool": tool_name}

    async def _calendar_tool(self, params: dict) -> dict:
        from tools.calendar_tool import create_calendar_events
        scheduled_days = params.get("scheduled_days", [])
        user = params.get("user")
        if not user:
            return {"status": "error", "message": "Authenticated user context is required."}
        result = await create_calendar_events(scheduled_days, user)
        return {"status": "success", "message": result}

    async def _task_manager_tool(self, params: dict) -> dict:
        from tools.task_manager_tool import create_tasks
        tasks = params.get("tasks", [])
        result = await create_tasks(tasks)
        return {"status": "success", "message": result, "mocked": True}

    async def _summary_tool(self, params: dict) -> dict:
        from tools.summary_tool import store_daily_summary
        result = await store_daily_summary(
            user_id=params.get("user_id", ""),
            summary=params.get("summary", ""),
            state=params.get("state", "normal"),
            plan_date=params.get("date", "")
        )
        return {"status": "success", "message": result}

    async def _productivity_tool(self, params: dict) -> dict:
        from tools.task_manager_tool import get_productivity_score
        result = await get_productivity_score(
            user_id=params.get("user_id", ""),
            capacity_used=params.get("capacity_used", 0)
        )
        return {"status": "success", "data": result, "mocked": True}


mcp_server = MCPServer()
