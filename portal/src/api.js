/**
 * InDiiServe Hospital Portal API Client
 * Enforces automatic CSRF token attachment, HttpOnly cookie credentials, and structured error handling.
 */

function getCookie(name) {
  const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
  return match ? decodeURIComponent(match[2]) : null;
}

export async function apiRequest(endpoint, options = {}) {
  const url = endpoint.startsWith('/api') ? endpoint : `/api/v1${endpoint}`;
  
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  };

  // Attach CSRF Token for mutating methods
  const method = (options.method || 'GET').toUpperCase();
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    const csrfToken = getCookie('indiiserve_csrf_token');
    if (csrfToken) {
      headers['X-CSRF-Token'] = csrfToken;
    }
  }

  const response = await fetch(url, {
    ...options,
    headers,
    credentials: 'include', // Automatically send and receive HttpOnly cookies
  });

  if (response.status === 401 && !endpoint.includes('/auth/login')) {
    // Session expired or unauthorized
    window.dispatchEvent(new CustomEvent('auth:unauthorized'));
  }

  const data = await response.json().catch(() => ({}));
  
  if (!response.ok) {
    const error = new Error(data.detail || `Request failed with status ${response.status}`);
    error.status = response.status;
    error.data = data;
    throw error;
  }

  return data;
}

export const api = {
  // Auth
  login: (username, password) => apiRequest('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  logout: () => apiRequest('/auth/logout', { method: 'POST' }),
  getMe: () => apiRequest('/auth/me'),
  refresh: () => apiRequest('/auth/refresh', { method: 'POST' }),
  inviteUser: (userData) => apiRequest('/auth/invite', { method: 'POST', body: JSON.stringify(userData) }),

  // Dashboard
  getDashboardStats: () => apiRequest('/dashboard/stats'),

  // Calls
  getCalls: (params = '') => apiRequest(`/calls${params ? '?' + params : ''}`),
  getCallTranscript: (sessionId) => apiRequest(`/calls/${sessionId}`),
  unmaskPhone: (sessionId) => apiRequest(`/calls/${sessionId}/unmask-phone`, { method: 'POST' }),

  // Appointments
  getAppointments: (params = '') => apiRequest(`/appointments${params ? '?' + params : ''}`),
  cancelAppointment: (appointmentId, idempotencyKey, reason) =>
    apiRequest('/appointments/cancel', {
      method: 'POST',
      body: JSON.stringify({ appointment_id: appointmentId, idempotency_key: idempotencyKey, reason }),
    }),

  // Triage
  getTriageEvents: (params = '') => apiRequest(`/triage${params ? '?' + params : ''}`),
  acknowledgeTriage: (eventId, assignedDoctorId, notes) =>
    apiRequest(`/triage/${eventId}/acknowledge`, {
      method: 'PATCH',
      body: JSON.stringify({ assigned_doctor_id: assignedDoctorId, notes }),
    }),
  resolveTriage: (eventId, clinicalNotes) =>
    apiRequest(`/triage/${eventId}/resolve`, {
      method: 'PATCH',
      body: JSON.stringify({ clinical_notes: clinicalNotes }),
    }),

  // Knowledge & Distiller
  getPendingFacts: () => apiRequest('/knowledge/review-facts'),
  approveFact: (factId) => apiRequest(`/knowledge/review-facts/${factId}/approve`, { method: 'POST', body: JSON.stringify({ fact_id: factId }) }),
  rejectFact: (factId) => apiRequest(`/knowledge/review-facts/${factId}/reject`, { method: 'POST', body: JSON.stringify({ fact_id: factId }) }),
  uploadKnowledgeDoc: (docData) => apiRequest('/knowledge/upload-document', { method: 'POST', body: JSON.stringify(docData) }),
  getRoster: () => apiRequest('/knowledge/roster'),
  updateDoctorRoster: (doctorId, data) => apiRequest(`/knowledge/roster/${doctorId}`, { method: 'PATCH', body: JSON.stringify(data) }),

  // Voice Sandbox
  simulateVoiceTurn: (simData) => apiRequest('/sandbox/simulate', { method: 'POST', body: JSON.stringify(simData) }),

  // Analytics
  getAnalyticsOverview: () => apiRequest('/analytics/overview'),
};

