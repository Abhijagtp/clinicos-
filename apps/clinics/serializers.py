import re
from zoneinfo import ZoneInfo
from rest_framework import serializers
from apps.clinics.models import Clinic


class ClinicSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = Clinic
        fields = [
            "id",
            "name",
            "phone_number",
            "timezone",
            "status",
            "default_appointment_duration",
            "slot_buffer_minutes",
            "min_notice_hours",
            "clinic_hours",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "status", "created_at", "updated_at"]

    def validate_timezone(self, value):
        try:
            ZoneInfo(value)
        except Exception:
            raise serializers.ValidationError(
                f"Invalid timezone '{value}'. Please provide a valid IANA timezone name (e.g. 'Asia/Kolkata', 'UTC')."
            )
        return value

    def validate_default_appointment_duration(self, value):
        if value < 10 or value > 240:
            raise serializers.ValidationError("Appointment duration must be between 10 and 240 minutes.")
        return value

    def validate_slot_buffer_minutes(self, value):
        if value < 0 or value > 60:
            raise serializers.ValidationError("Slot buffer must be between 0 and 60 minutes.")
        return value

    def validate_min_notice_hours(self, value):
        if value < 0 or value > 168:
            raise serializers.ValidationError("Minimum notice must be between 0 and 168 hours (7 days).")
        return value

    def validate_clinic_hours(self, value):
        """
        Validates structure of clinic_hours JSON:
        {
          "0": [{"start": "09:00", "end": "13:00"}],
          ...
          "6": []
        }
        """
        if not isinstance(value, dict):
            raise serializers.ValidationError("clinic_hours must be a dictionary keyed by weekday ('0' through '6').")

        time_pattern = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

        for day_str, windows in value.items():
            if str(day_str) not in {"0", "1", "2", "3", "4", "5", "6"}:
                raise serializers.ValidationError(f"Invalid weekday key '{day_str}'. Must be '0' through '6'.")
            if not isinstance(windows, list):
                raise serializers.ValidationError(f"Operating windows for day '{day_str}' must be a list.")

            # Validate each time interval
            parsed_intervals = []
            for item in windows:
                if not isinstance(item, dict) or "start" not in item or "end" not in item:
                    raise serializers.ValidationError(f"Each window for day {day_str} must have 'start' and 'end' time strings.")
                start = item["start"]
                end = item["end"]

                if not time_pattern.match(start) or not time_pattern.match(end):
                    raise serializers.ValidationError(f"Time format must be 'HH:MM' (24-hour). Received: {start} - {end}")

                s_hour, s_min = map(int, start.split(":"))
                e_hour, e_min = map(int, end.split(":"))
                s_total = s_hour * 60 + s_min
                e_total = e_hour * 60 + e_min

                if s_total >= e_total:
                    raise serializers.ValidationError(f"Window start ({start}) must be before window end ({end}).")

                parsed_intervals.append((s_total, e_total))

            # Ensure intervals on the same day do not overlap
            parsed_intervals.sort(key=lambda x: x[0])
            for i in range(len(parsed_intervals) - 1):
                if parsed_intervals[i][1] > parsed_intervals[i + 1][0]:
                    raise serializers.ValidationError(f"Overlapping operating intervals detected on weekday {day_str}.")

        return value
