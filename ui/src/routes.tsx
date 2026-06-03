import { Navigate, Route, Routes } from "react-router-dom";
import { RouteGuard } from "./components/RouteGuard";
import { AdminLayout } from "./layouts/AdminLayout";
import { BusinessLayout } from "./layouts/BusinessLayout";
import { Dashboard } from "./admin/Dashboard";
import { Uploads } from "./admin/Uploads";
import { Jobs } from "./admin/Jobs";
import { JobDetail } from "./admin/JobDetail";
import { Agents } from "./admin/Agents";
import { Profiles } from "./admin/Profiles";
import { Audit } from "./admin/Audit";
import { Costs } from "./admin/Costs";
import { Inbox } from "./business/Inbox";
import { RateSheetReview } from "./business/RateSheetReview";
import { ByUnion } from "./business/ByUnion";
import { Approved } from "./business/Approved";
import { Rejected } from "./business/Rejected";
import { ReviewQueue } from "./business/ReviewQueue";
import { Me } from "./business/Me";

const ADMIN = ["Admins", "Operations"];
const ADMINS_ONLY = ["Admins"];
const BUSINESS = ["Business"];

export function AppRoutes(): JSX.Element {
  return (
    <Routes>
      <Route
        path="/admin"
        element={
          <RouteGuard groups={ADMIN}>
            <AdminLayout />
          </RouteGuard>
        }
      >
        <Route index element={<Navigate to="dashboard" replace />} />
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="uploads" element={<Uploads />} />
        <Route path="jobs" element={<Jobs />} />
        <Route path="jobs/:id" element={<JobDetail />} />
        <Route path="agents" element={<Agents />} />
        <Route path="profiles" element={<Profiles />} />
        <Route path="audit" element={<Audit />} />
        <Route
          path="costs"
          element={
            <RouteGuard groups={ADMINS_ONLY}>
              <Costs />
            </RouteGuard>
          }
        />
      </Route>

      <Route
        path="/business"
        element={
          <RouteGuard groups={BUSINESS}>
            <BusinessLayout />
          </RouteGuard>
        }
      >
        <Route index element={<Navigate to="inbox" replace />} />
        <Route path="inbox" element={<Inbox />} />
        <Route path="rate-sheets/:union/:period" element={<RateSheetReview />} />
        <Route path="by-union/:union" element={<ByUnion />} />
        <Route path="approved" element={<Approved />} />
        <Route path="rejected" element={<Rejected />} />
        <Route path="queue" element={<ReviewQueue />} />
        <Route path="me" element={<Me />} />
      </Route>
    </Routes>
  );
}
