import { useEffect } from "react";
import { useAuth } from "./auth.jsx";
import { useDetectionSocket } from "./hooks/useDetectionSocket.js";
import AuthScreen from "./components/AuthScreen.jsx";
import Header from "./components/Header.jsx";
import DetectionPanel from "./components/DetectionPanel.jsx";
import ResultView from "./components/ResultView.jsx";
import { AlertsCard, ProfileCard } from "./components/Sidebar.jsx";

function Dashboard() {
  const { token, clearSession } = useAuth();
  const socket = useDetectionSocket(token, { onUnauthorized: clearSession });
  const { connect } = socket;

  // Connexion automatique au WebSocket une fois authentifié.
  useEffect(() => {
    connect();
  }, [connect]);

  return (
    <>
      <Header />
      <main className="dashboard">
        <div className="main-column">
          <DetectionPanel socket={socket} />
          <ResultView result={socket.result} pending={socket.pending} />
        </div>
        <aside className="side-column">
          <AlertsCard alerts={socket.alerts} onClear={socket.clearAlerts} />
          <ProfileCard />
        </aside>
      </main>
    </>
  );
}

export default function App() {
  const { token, loading } = useAuth();
  if (loading) {
    return (
      <div className="splash">
        <span className="spinner" aria-label="Chargement" />
      </div>
    );
  }
  return token ? <Dashboard /> : <AuthScreen />;
}
