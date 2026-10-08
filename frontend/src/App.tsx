import { useState, useMemo } from "react";
import { AuthProvider, useAuth } from "./contexts/AuthContext";
import { Sidebar, TopBar, MobileTabs } from "./components/layout";
import { Spinner } from "./components/ui";
import { ActivityToaster } from "./components/ActivityToaster";
import { ErrorBoundary } from "./components/ErrorBoundary";

import LoginView from "./views/LoginView";
import OverviewView from "./views/OverviewView";
import OperationsView from "./views/OperationsView";
import IntelligenceView from "./views/IntelligenceView";
import AssetsView from "./views/AssetsView";
import AdminView from "./views/AdminView";
import EmployeeView from "./views/EmployeeView";
import AttendanceView from "./views/AttendanceView";
import MessView from "./views/MessView";
import TruckCountView from "./views/TruckCountView";
import AlertsView from "./views/AlertsView";
import DevicesView from "./views/DevicesView";
import NvrDvrView from "./views/NvrDvrView";
import RtspStringsView from "./views/RtspStringsView";
import UsersView from "./views/UsersView";
import ChatView from "./views/ChatView";

function Dashboard() {
  const { user } = useAuth();
  const [active, setActive] = useState("overview");
  const [collapsed, setCollapsed] = useState(false);

  const view = useMemo(() => {
    switch (active) {
      case "operations": return <OperationsView />;
      case "intelligence": return <IntelligenceView />;
      case "assets": return <AssetsView />;
      case "chat": return <ChatView />;
      case "ip_cameras": return <DevicesView />;
      case "nvr_dvr": return <NvrDvrView />;
      case "rtsp_strings": return <RtspStringsView />;
      case "admin": return <AdminView />;
      case "employees": return <EmployeeView />;
      case "attendance": return <AttendanceView />;
      case "mess": return <MessView />;
      case "truck_count": return <TruckCountView onNavigate={setActive} />;
      case "alerts": return <AlertsView />;
      case "users": return user?.role === "admin" ? <UsersView /> : <OverviewView />;
      default: return <OverviewView />;
    }
  }, [active, user]);

  return (
    <div className="min-h-screen bg-base text-tx">
      <div className="flex min-h-screen flex-col xl:flex-row">
        <Sidebar active={active} setActive={setActive} collapsed={collapsed} setCollapsed={setCollapsed} />
        <main className="flex-1 bg-content p-4 lg:p-6 xl:p-8">
          <div className="mx-auto max-w-[1680px] space-y-6">
            <TopBar collapsed={collapsed} setCollapsed={setCollapsed} />
            <MobileTabs active={active} setActive={setActive} />
            <section key={active} className="animate-[fadeIn_0.25s_ease-out]">
              <ErrorBoundary key={active}>{view}</ErrorBoundary>
            </section>
          </div>
        </main>
      </div>
      <ActivityToaster activeView={active} />
    </div>
  );
}

function AppInner() {
  const { user, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-base">
        <Spinner size="lg" />
      </div>
    );
  }

  if (!user) {
    return <LoginView />;
  }

  return <Dashboard />;
}

export default function App() {
  return (
    <AuthProvider>
      <AppInner />
    </AuthProvider>
  );
}
