import PortalHome from "../shared/PortalHome.jsx";

// /admin — Admin portal: property operations (units, billing & payments, water, reports, operations, community).
export default function Home() {
  return (
    <PortalHome
      eyebrow="ADMIN PORTAL"
      title="Property Operations"
      intro="Manage units, tenants, billing, payments, water readings and day-to-day operations."
    />
  );
}
