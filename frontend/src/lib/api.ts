/**
 * Authenticated API client for backend requests.
 * Automatically attaches Supabase JWT token to all requests.
 */
import { createClient } from "@/lib/supabase/client";

const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:3000").replace(/\/+$/, "");

/**
 * Make an authenticated fetch request to the backend API.
 * Automatically retrieves and attaches the current user's JWT.
 */
export async function apiFetch(
    endpoint: string,
    options: RequestInit = {}
): Promise<Response> {
    const supabase = createClient();
    const {
        data: { session },
    } = await supabase.auth.getSession();

    const headers: Record<string, string> = {
        "Content-Type": "application/json",
        ...(options.headers as Record<string, string>),
    };

    // Attach JWT if user is authenticated
    if (session?.access_token) {
        headers["Authorization"] = `Bearer ${session.access_token}`;
    }

    const url = endpoint.startsWith("http")
        ? endpoint
        : `${API_URL}${endpoint.startsWith("/") ? "" : "/"}${endpoint}`;

    return fetch(url, {
        ...options,
        headers,
    });
}

/**
 * Submit a video for processing.
 * Backend will extract userId from the verified JWT — no need to send it.
 */
export async function submitVideo(videoUrl: string) {
    const response = await apiFetch("/process", {
        method: "POST",
        body: JSON.stringify({ videoUrl }),
    });

    if (!response.ok) {
        const error = await response.json().catch(() => ({ message: "Unknown error" }));
        throw new Error(error.message || error.error || `HTTP ${response.status}`);
    }

    return response.json();
}

/**
 * Get job status and results.
 */
export async function getJobStatus(jobId: string) {
    const response = await apiFetch(`/status/${jobId}`);

    if (!response.ok) {
        const error = await response.json().catch(() => ({ message: "Unknown error" }));
        throw new Error(error.message || error.error || `HTTP ${response.status}`);
    }

    return response.json();
}

/**
 * Get current user's credits.
 */
export async function getUserCredits() {
    // The backend no longer uses the userId from the URL (it uses the JWT)
    // but we keep the path for backwards compatibility
    const response = await apiFetch("/user/me/credits");

    if (!response.ok) {
        const error = await response.json().catch(() => ({ message: "Unknown error" }));
        throw new Error(error.message || error.error || `HTTP ${response.status}`);
    }

    return response.json();
}
