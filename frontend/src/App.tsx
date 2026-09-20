import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { useInvalidateOnFlush } from "./lib/hooks";
import { Log } from "./screens/Log";
import { Progress } from "./screens/Progress";
import { Settings } from "./screens/Settings";
import { Summary } from "./screens/Summary";
import { Today } from "./screens/Today";
import { Train } from "./screens/Train";
import { Weight } from "./screens/Weight";

export function App() {
  useInvalidateOnFlush();
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Today />} />
        <Route path="/log" element={<Log />} />
        <Route path="/weight" element={<Weight />} />
        <Route path="/train" element={<Train />} />
        <Route path="/progress" element={<Progress />} />
        <Route path="/summary" element={<Summary />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
