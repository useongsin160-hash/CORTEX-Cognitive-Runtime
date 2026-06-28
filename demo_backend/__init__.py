"""CORTEX-AEV Agent Planner Demo backend (D1).

CORTEX core(app/)를 절대 import 하지 않는다. CORTEX는 httpx 프록시로 HTTP 호출만 한다.
분리 배포: 순수 JSON API 서버(HTML/StaticFiles 서빙 없음), 프론트는 cross-origin 호출.
"""

__version__ = "0.1.0-d1"
