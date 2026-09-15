/**
 * Client-side printable PDF appointment slip generator.
 * Produces clean, professional medical appointment passes ready for printing or digital archiving.
 */

export function printAppointmentSlip(appointment) {
  const printWindow = window.open('', '_blank');
  if (!printWindow) {
    alert('Please allow popups to print the appointment pass.');
    return;
  }

  const patientName = appointment.patient_name || 'Valued Patient';
  const doctorName = appointment.doctor_name || 'Consultant Physician';
  const department = appointment.department || 'General Medicine';
  const visitDateTime = appointment.visit_datetime || 'Upcoming Slot';
  const appointmentId = appointment.appointment_id || 'REF-APP-XXXX';
  const callerPhone = appointment.caller_phone || '+91 9876543210';
  const fee = appointment.fee || '₹500';
  const room = appointment.room || 'OPD 104';

  const htmlContent = `
    <!DOCTYPE html>
    <html>
      <head>
        <title>OPD Appointment Pass — ${appointmentId}</title>
        <style>
          body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            color: #0f172a;
            margin: 0;
            padding: 24px;
            background: #ffffff;
          }
          .pass-container {
            max-width: 600px;
            margin: 0 auto;
            border: 2px solid #0284c7;
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.05);
          }
          .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 2px dashed #cbd5e1;
            padding-bottom: 16px;
            margin-bottom: 20px;
          }
          .hospital-title {
            font-size: 20px;
            font-weight: 800;
            color: #0284c7;
            margin: 0;
          }
          .hospital-sub {
            font-size: 11px;
            color: #64748b;
            margin-top: 2px;
          }
          .badge {
            background: #e0f2fe;
            color: #0369a1;
            padding: 4px 12px;
            border-radius: 9999px;
            font-size: 11px;
            font-weight: bold;
            text-transform: uppercase;
          }
          .grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
            margin-bottom: 20px;
          }
          .field-label {
            font-size: 10px;
            text-transform: uppercase;
            font-weight: bold;
            color: #64748b;
            margin-bottom: 4px;
          }
          .field-value {
            font-size: 14px;
            font-weight: 700;
            color: #0f172a;
          }
          .instructions {
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 12px;
            padding: 12px;
            font-size: 11px;
            color: #334155;
            margin-bottom: 20px;
            line-height: 1.5;
          }
          .footer {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-top: 1px solid #e2e8f0;
            padding-top: 16px;
            font-size: 10px;
            color: #94a3b8;
          }
          .qr-placeholder {
            border: 2px solid #0284c7;
            padding: 6px 12px;
            border-radius: 8px;
            font-family: monospace;
            font-weight: bold;
            color: #0284c7;
            background: #f0f9ff;
          }
          @media print {
            body { padding: 0; }
            .pass-container { border: 1px solid #000; box-shadow: none; }
          }
        </style>
      </head>
      <body>
        <div class="pass-container">
          <div class="header">
            <div>
              <h1 class="hospital-title">Apollo Metro Hospital</h1>
              <p class="hospital-sub">Department of Clinical Services • ASHA Voice Booking</p>
            </div>
            <span class="badge">CONFIRMED OPD PASS</span>
          </div>

          <div class="grid">
            <div>
              <div class="field-label">Reference ID</div>
              <div class="field-value" style="font-family: monospace; color: #0284c7;">${appointmentId}</div>
            </div>
            <div>
              <div class="field-label">Visit Date & Time</div>
              <div class="field-value">${visitDateTime}</div>
            </div>
            <div>
              <div class="field-label">Patient Name</div>
              <div class="field-value">${patientName} (${callerPhone})</div>
            </div>
            <div>
              <div class="field-label">Consulting Doctor</div>
              <div class="field-value">${doctorName}</div>
            </div>
            <div>
              <div class="field-label">OPD Room / Suite</div>
              <div class="field-value">${room} (${department})</div>
            </div>
            <div>
              <div class="field-label">Consultation Fee</div>
              <div class="field-value">${fee}</div>
            </div>
          </div>

          <div class="instructions">
            <strong>Patient Guidelines:</strong><br/>
            • Please arrive 15 minutes before your scheduled appointment time.<br/>
            • Bring all previous medical records, prescriptions, and radiology films.<br/>
            • Show this digital pass or reference ID at the OPD reception desk for direct queue check-in.
          </div>

          <div class="footer">
            <div>
              Generated via InDiiServe ASHA Voice Engine<br/>
              Apollo Metro Hospital • 24/7 Helpline: 09513886363
            </div>
            <div class="qr-placeholder">
              [ SCAN QR: ${appointmentId} ]
            </div>
          </div>
        </div>
        <script>
          window.onload = function() {
            window.print();
          };
        </script>
      </body>
    </html>
  `;

  printWindow.document.write(htmlContent);
  printWindow.document.close();
}
