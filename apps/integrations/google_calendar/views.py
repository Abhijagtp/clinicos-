import logging
import urllib.parse
from django.conf import settings
from django.shortcuts import redirect
from django.http import JsonResponse, HttpResponse
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from apps.clinics.models import Clinic, ClinicMembership
from .models import GoogleCalendarConfig

logger = logging.getLogger(__name__)
signer = TimestampSigner()


class GoogleCalendarConnectView(APIView):
    """
    Initiates Google OAuth 2.0 flow for authorized clinic staff.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        client_id = getattr(settings, "GOOGLE_CLIENT_ID", "")
        if not client_id:
            return Response(
                {"error": "Google Calendar integration is not configured with GOOGLE_CLIENT_ID."},
                status=500,
            )

        # Determine target clinic
        clinic_id = request.query_params.get("clinic_id")
        membership = None
        if clinic_id:
            membership = ClinicMembership.objects.filter(
                clinic_id=clinic_id,
                user=request.user,
                is_active=True,
                role__in=[ClinicMembership.Role.OWNER, ClinicMembership.Role.DOCTOR],
            ).first()
        else:
            membership = ClinicMembership.objects.filter(
                user=request.user,
                is_active=True,
                role__in=[ClinicMembership.Role.OWNER, ClinicMembership.Role.DOCTOR],
            ).first()

        if not membership and not request.user.is_superuser:
            return Response({"error": "You do not have administrative permissions for this clinic."}, status=403)

        target_clinic = membership.clinic if membership else Clinic.objects.first()
        if not target_clinic:
            return Response({"error": "No clinic found."}, status=404)

        # Generate signed state token (valid for 15 minutes)
        state_data = f"{target_clinic.id}:{request.user.id}"
        signed_state = signer.sign(state_data)

        redirect_uri = request.build_absolute_uri("/api/v1/integrations/google-calendar/callback/")
        scope = "https://www.googleapis.com/auth/calendar"

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scope,
            "access_type": "offline",
            "prompt": "consent",
            "state": signed_state,
        }
        oauth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"

        return Response({
            "authorization_url": oauth_url,
            "clinic_id": str(target_clinic.id),
        })


class GoogleCalendarCallbackView(APIView):
    """
    Handles OAuth 2.0 callback, exchanges auth code for refresh token, and updates GoogleCalendarConfig.
    """
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        error = request.query_params.get("error")

        if error:
            logger.error(f"Google OAuth authorization error: {error}")
            return Response({"error": f"OAuth authorization failed: {error}"}, status=400)

        if not code or not state:
            return Response({"error": "Missing authorization code or state parameter."}, status=400)

        try:
            state_data = signer.unsign(state, max_age=900)  # 15 minutes
            clinic_id, user_id = state_data.split(":", 1)
        except (BadSignature, SignatureExpired, ValueError):
            return Response({"error": "Invalid or expired state parameter."}, status=400)

        clinic = Clinic.objects.filter(id=clinic_id).first()
        if not clinic:
            return Response({"error": "Clinic not found."}, status=404)

        client_id = getattr(settings, "GOOGLE_CLIENT_ID", "")
        client_secret = getattr(settings, "GOOGLE_CLIENT_SECRET", "")
        redirect_uri = request.build_absolute_uri("/api/v1/integrations/google-calendar/callback/")

        # Exchange authorization code for tokens
        import requests
        token_url = "https://oauth2.googleapis.com/token"
        data = {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }

        try:
            token_res = requests.post(token_url, data=data, timeout=10)
            if not token_res.ok:
                logger.error(f"Failed to exchange Google OAuth code: {token_res.text}")
                return Response({"error": "Failed to exchange Google OAuth authorization code."}, status=400)

            token_data = token_res.json()
            refresh_token = token_data.get("refresh_token", "")
            access_token = token_data.get("access_token", "")

            config, _ = GoogleCalendarConfig.objects.get_or_create(clinic=clinic)
            if refresh_token:
                config.refresh_token = refresh_token
            if access_token:
                config.access_token = access_token
            config.is_connected = True
            config.save()

            return Response({
                "status": "connected",
                "clinic_id": str(clinic.id),
                "message": "Google Calendar successfully connected.",
            })
        except requests.RequestException as exc:
            logger.error(f"Network error in OAuth token exchange: {exc}")
            return Response({"error": "Network error during token exchange."}, status=500)
