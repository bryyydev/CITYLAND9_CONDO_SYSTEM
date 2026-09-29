import PortalHome from "../shared/PortalHome.jsx";

// /hr — Manager portal: HR and Philippine payroll (SSS, PhilHealth, Pag-IBIG, BIR) plus announcements.
export default function Home() {
  return (
    <PortalHome
      eyebrow="HR & PAYROLL PORTAL"
      title="Human Resources & Payroll"
      intro="Manage employees, attendance, leave, overtime and payroll."
    />
  );
}
