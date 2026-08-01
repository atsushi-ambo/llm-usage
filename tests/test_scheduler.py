"""Tests for scheduled reports system."""

from datetime import datetime, timezone
from llm_usage.scheduler import (
    ScheduleFrequency,
    ScheduledReport,
    calculate_next_run,
    save_schedule,
    load_schedule,
    delete_schedule,
    list_schedules,
)


def test_scheduled_report_creation():
    """Test creating a scheduled report."""
    schedule = ScheduledReport(
        name="test_schedule",
        frequency=ScheduleFrequency.DAILY,
        days=30,
        export_format="csv",
    )
    assert schedule.name == "test_schedule"
    assert schedule.frequency == ScheduleFrequency.DAILY
    assert schedule.days == 30
    assert schedule.export_format == "csv"
    assert schedule.enabled is True


def test_calculate_next_run_daily():
    """Test calculating next run for daily frequency."""
    last_run = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    next_run = calculate_next_run(ScheduleFrequency.DAILY, last_run)
    assert next_run.day == 2


def test_calculate_next_run_weekly():
    """Test calculating next run for weekly frequency."""
    last_run = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    next_run = calculate_next_run(ScheduleFrequency.WEEKLY, last_run)
    assert next_run.day == 8


def test_calculate_next_run_monthly():
    """Test calculating next run for monthly frequency."""
    last_run = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    next_run = calculate_next_run(ScheduleFrequency.MONTHLY, last_run)
    assert next_run.month == 2


def test_calculate_next_run_no_last_run():
    """Test calculating next run when no last run provided."""
    next_run = calculate_next_run(ScheduleFrequency.DAILY, None)
    assert next_run is not None
    assert isinstance(next_run, datetime)


def test_save_and_load_schedule():
    """Test saving and loading a schedule."""
    schedule = ScheduledReport(
        name="test_save_load",
        frequency=ScheduleFrequency.WEEKLY,
        days=7,
        export_format="json",
    )
    schedule.next_run = calculate_next_run(ScheduleFrequency.WEEKLY)
    
    save_schedule(schedule)
    loaded = load_schedule("test_save_load")
    
    assert loaded is not None
    assert loaded.name == "test_save_load"
    assert loaded.frequency == ScheduleFrequency.WEEKLY
    assert loaded.days == 7
    assert loaded.export_format == "json"
    
    # Cleanup
    delete_schedule("test_save_load")


def test_delete_schedule():
    """Test deleting a schedule."""
    schedule = ScheduledReport(
        name="test_delete",
        frequency=ScheduleFrequency.DAILY,
        days=30,
    )
    save_schedule(schedule)
    
    assert delete_schedule("test_delete") is True
    assert load_schedule("test_delete") is None


def test_delete_nonexistent_schedule():
    """Test deleting a schedule that doesn't exist."""
    assert delete_schedule("nonexistent_schedule") is False


def test_list_schedules():
    """Test listing all schedules."""
    # Create test schedules
    schedule1 = ScheduledReport(
        name="test_list_1",
        frequency=ScheduleFrequency.DAILY,
        days=30,
    )
    schedule2 = ScheduledReport(
        name="test_list_2",
        frequency=ScheduleFrequency.WEEKLY,
        days=7,
    )
    
    save_schedule(schedule1)
    save_schedule(schedule2)
    
    schedules = list_schedules()
    schedule_names = [s.name for s in schedules]
    
    assert "test_list_1" in schedule_names
    assert "test_list_2" in schedule_names
    
    # Cleanup
    delete_schedule("test_list_1")
    delete_schedule("test_list_2")
