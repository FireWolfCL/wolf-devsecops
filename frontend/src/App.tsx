import { Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { RequireAuth, RequireRole } from "./auth/guards";
import { About } from "./pages/About";
import { Plans } from "./pages/Plans";
import { AdminLayout } from "./components/AdminLayout";
import { Layout } from "./components/Layout";
import { Herramientas } from "./pages/Herramientas";
import { AIConfig } from "./pages/AIConfig";
import { Integraciones } from "./pages/Integraciones";
import { Login } from "./pages/Login";
import { Profile } from "./pages/Profile";
import { Settings } from "./pages/Settings";
import { UpdateCenter } from "./pages/UpdateCenter";
import { Users } from "./pages/Users";
import { ProjectDetailView } from "./views/ProjectDetailView";
import { ProjectListView } from "./views/ProjectListView";
import { CodeReviewView } from "./views/CodeReviewView";
import { CodeMapView } from "./views/CodeMapView";
import { DependencyInventoryView } from "./views/DependencyInventoryView";
import { PostureHistoryView } from "./views/PostureHistoryView";
import { ReportView } from "./views/ReportView";
import { ActivityView } from "./views/ActivityView";

function Dashboard() {
  const navigate = useNavigate();
  return <ProjectListView onSelect={(key) => navigate(`/projects/${encodeURIComponent(key)}`)} />;
}

function ProjectDetailRoute() {
  const { key } = useParams<{ key: string }>();
  const navigate = useNavigate();
  if (!key) return <Navigate to="/" replace />;
  return <ProjectDetailView projectKey={key} onBack={() => navigate("/")} />;
}

function ReportRoute() {
  const { key } = useParams<{ key: string }>();
  const navigate = useNavigate();
  if (!key) return <Navigate to="/" replace />;
  return <ReportView projectKey={key} onBack={() => navigate(`/projects/${encodeURIComponent(key)}`)} />;
}

function DepsRoute() {
  const { key } = useParams<{ key: string }>();
  const navigate = useNavigate();
  if (!key) return <Navigate to="/" replace />;
  return <DependencyInventoryView projectKey={key} onBack={() => navigate(`/projects/${encodeURIComponent(key)}`)} />;
}

function CodeMapRoute() {
  const { key } = useParams<{ key: string }>();
  const navigate = useNavigate();
  if (!key) return <Navigate to="/" replace />;
  return <CodeMapView projectKey={key} onBack={() => navigate(`/projects/${encodeURIComponent(key)}`)} />;
}

function CodeReviewRoute() {
  const { key } = useParams<{ key: string }>();
  const navigate = useNavigate();
  if (!key) return <Navigate to="/" replace />;
  return <CodeReviewView projectKey={key} onBack={() => navigate(`/projects/${encodeURIComponent(key)}`)} />;
}

function HistoryRoute() {
  const { key } = useParams<{ key: string }>();
  const navigate = useNavigate();
  if (!key) return <Navigate to="/" replace />;
  return <PostureHistoryView projectKey={key} onBack={() => navigate(`/projects/${encodeURIComponent(key)}`)} />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/plans" element={<Plans />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="tools" element={<Herramientas />} />
        <Route path="activity" element={<ActivityView />} />
        <Route path="projects/:key" element={<ProjectDetailRoute />} />
        <Route path="projects/:key/report" element={<ReportRoute />} />
        <Route path="projects/:key/deps" element={<DepsRoute />} />
        <Route path="projects/:key/history" element={<HistoryRoute />} />
        <Route path="projects/:key/codemap" element={<CodeMapRoute />} />
        <Route path="projects/:key/code-review" element={<CodeReviewRoute />} />
        <Route path="profile" element={<Profile />} />
        <Route path="admin" element={<RequireRole role="admin"><AdminLayout /></RequireRole>}>
          <Route index element={<Navigate to="/admin/users" replace />} />
          <Route path="users" element={<Users />} />
          <Route path="integraciones" element={<Integraciones />} />
          <Route path="settings" element={<Settings />} />
          <Route path="ai" element={<AIConfig />} />
          <Route path="updates" element={<UpdateCenter />} />
          <Route path="about" element={<About />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
