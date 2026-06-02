from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eve_voice_pilot.trade_agent import (
    DistributionRunPlan,
    EveWorkbenchTradeClient,
    ItemMetadata,
    RouteSystem,
    SolarSystem,
    TradeAgentError,
    TradeOpportunity,
    TradeOrder,
    TradePlan,
    format_plan,
    plan_distribution_run,
)


class FakeMetadataClient:
    def warm_type_metadata(self, type_ids):
        self.warmed = tuple(type_ids)

    def get_type_metadata(self, type_id):
        if type_id == 1:
            return ItemMetadata(type_id, "Tungsten Charge L", 85, "Hybrid Charge", 8, "Charge")
        return ItemMetadata(type_id, "Fake Ship", 25, "Frigate", 6, "Ship")


class FakeTradeClient(EveWorkbenchTradeClient):
    def __init__(self):
        self.systems = {
            "jita": SolarSystem("Jita", 30000142),
            "amarr": SolarSystem("Amarr", 30002187),
            "perimeter": SolarSystem("Perimeter", 30000144),
        }

    def resolve_system(self, query: str) -> SolarSystem:
        try:
            return self.systems[query.casefold()]
        except KeyError as exc:
            raise TradeAgentError(f"No EVE system matched {query!r}.") from exc

    def run_trade_tool(self, origin, destination, *, run_type="sell-buy", volume=10000):
        route = (RouteSystem(origin.name, 0.9), RouteSystem(destination.name, 0.8))
        if destination.name == "Amarr":
            route = (
                RouteSystem(origin.name, 0.9),
                RouteSystem("Middle", 0.8),
                RouteSystem(destination.name, 0.8),
            )
        if destination.name == "Perimeter":
            route = (RouteSystem(origin.name, 0.9), RouteSystem(destination.name, 0.9))
        return TradePlan(
            origin=origin,
            destination=destination,
            route=route,
            opportunities=(
                TradeOpportunity(
                    type_id=1,
                    type_name=f"{destination.name} Good Margin",
                    packaged_volume=5,
                    isk_per_jump=2500 if destination.name == "Amarr" else 500,
                    isk_per_m3=1000,
                    max_quantity=2,
                    max_total_volume=10,
                    price_diff=10000,
                    from_order=TradeOrder(100000, 60003760, "Jita IV", 3, 5),
                    to_order=TradeOrder(110000, 60008494, f"{destination.name} Hub", 4, 8),
                ),
                TradeOpportunity(
                    type_id=2,
                    type_name=f"{destination.name} Negative Score",
                    packaged_volume=1,
                    isk_per_jump=-5,
                    isk_per_m3=1,
                    max_quantity=1,
                    max_total_volume=1,
                    price_diff=1,
                    from_order=TradeOrder(1, 1, "A", 1, 1),
                    to_order=TradeOrder(2, 2, "B", 1, 1),
                ),
            ),
        )


class FakeMixedTradeClient(FakeTradeClient):
    def run_trade_tool(self, origin, destination, *, run_type="sell-buy", volume=10000):
        route = (RouteSystem(origin.name, 0.9), RouteSystem(destination.name, 0.8))
        return TradePlan(
            origin=origin,
            destination=destination,
            route=route,
            opportunities=(
                TradeOpportunity(
                    type_id=1,
                    type_name="Tungsten Charge L",
                    packaged_volume=0.025,
                    isk_per_jump=2500,
                    isk_per_m3=1000,
                    max_quantity=100,
                    max_total_volume=2.5,
                    price_diff=10,
                    from_order=TradeOrder(20, 60003760, "Jita IV", 100, 100),
                    to_order=TradeOrder(30, 60008494, "Amarr Hub", 100, 100),
                ),
                TradeOpportunity(
                    type_id=3,
                    type_name="Fake Ship",
                    packaged_volume=2500,
                    isk_per_jump=2500,
                    isk_per_m3=1000,
                    max_quantity=1,
                    max_total_volume=2500,
                    price_diff=10000,
                    from_order=TradeOrder(100000, 60003760, "Jita IV", 1, 1),
                    to_order=TradeOrder(110000, 60008494, "Amarr Hub", 1, 1),
                ),
            ),
        )


def test_plan_distribution_run_ranks_positive_opportunities():
    plan = plan_distribution_run(
        FakeTradeClient(),
        from_system="Jita",
        to_system="Amarr",
        volume=10000,
        top=3,
    )
    assert plan.origin.name == "Jita"
    assert [item.opportunity.type_name for item in plan.ranked] == ["Amarr Good Margin"]


def test_plan_distribution_run_caps_quantity_to_budget():
    plan = plan_distribution_run(
        FakeTradeClient(),
        from_system="Jita",
        to_system="Amarr",
        volume=10000,
        budget=150000,
        top=3,
    )
    assert plan.ranked[0].quantity == 1
    assert plan.ranked[0].buy_total == 100000
    assert plan.ranked[0].total_profit == 10000


def test_plan_distribution_run_filters_industrial_domain():
    plan = plan_distribution_run(
        FakeMixedTradeClient(),
        from_system="Jita",
        to_system="Amarr",
        volume=10000,
        top=3,
        item_domain="industrial",
        metadata_client=FakeMetadataClient(),
    )
    assert [item.opportunity.type_name for item in plan.ranked] == ["Tungsten Charge L"]


def test_plan_distribution_run_filters_distance_targets():
    plan = plan_distribution_run(
        FakeTradeClient(),
        from_system="Jita",
        max_jumps=1,
        volume=10000,
        top=5,
        target_names=["Amarr", "Perimeter"],
    )
    assert [item.destination.name for item in plan.ranked] == ["Perimeter"]
    assert plan.skipped == ("Amarr: 2 jumps exceeds max 1",)


def test_format_plan_explains_suggestion_reason():
    plan = DistributionRunPlan(
        origin=SolarSystem("Jita", 30000142),
        checked_destinations=(
            TradePlan(
                origin=SolarSystem("Jita", 30000142),
                destination=SolarSystem("Amarr", 30002187),
                route=(RouteSystem("Jita", 0.9), RouteSystem("Amarr", 0.9)),
                opportunities=(),
            ),
        ),
        ranked=(
            plan_distribution_run(
                FakeTradeClient(),
                from_system="Jita",
                to_system="Amarr",
                volume=10000,
                top=1,
            ).ranked[0],
        ),
        skipped=(),
    )
    output = format_plan(plan, volume=10000, sort_by="isk-per-jump")
    assert "Suggested buys:" in output
    assert "Why: positive spread" in output
    assert "Check orders in game before hauling" in output


def test_format_plan_compact_keeps_trade_numbers():
    plan = plan_distribution_run(
        FakeTradeClient(),
        from_system="Jita",
        to_system="Amarr",
        volume=10000,
        budget=150000,
        top=1,
    )
    output = format_plan(plan, volume=10000, sort_by="isk-per-jump", budget=150000, output_format="compact")
    assert "spend 100.00k ISK" in output
    assert "profit 10.00k ISK" in output
    assert "ROI 10.0%" in output
