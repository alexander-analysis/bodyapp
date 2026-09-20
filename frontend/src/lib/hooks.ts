import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { get } from "./api";
import { onFlushed } from "./outbox";
import type { Exercise, Favorite, Health, History, NextSession, ReviewPayload, Summary, Template, Today, TrendPayload, Volume, Workout } from "./types";

export const useToday = () => useQuery({ queryKey: ["today"], queryFn: () => get<Today>("/api/v1/today") });
export const useTrend = (days: number) =>
  useQuery({ queryKey: ["trend", days], queryFn: () => get<TrendPayload>(`/api/v1/weight/trend?days=${days}`) });
export const useFavorites = () =>
  useQuery({ queryKey: ["favorites"], queryFn: () => get<{ favorites: Favorite[]; suggestions: import("./types").Suggestion[] }>("/api/v1/food/favorites") });
export const useExercises = () =>
  useQuery({ queryKey: ["exercises"], queryFn: () => get<{ exercises: Exercise[]; templates: Template[] }>("/api/v1/exercises") });
export const useNextSession = (template: string | null) =>
  useQuery({
    queryKey: ["next", template],
    queryFn: () => get<NextSession>(`/api/v1/workout/next${template ? `?template=${encodeURIComponent(template)}` : ""}`),
  });
export const useHistory = (exerciseId: number | null) =>
  useQuery({ queryKey: ["history", exerciseId], queryFn: () => get<History>(`/api/v1/workout/history/${exerciseId}`), enabled: exerciseId != null });
export const useVolume = () => useQuery({ queryKey: ["volume"], queryFn: () => get<Volume>("/api/v1/volume/weekly") });
export const useSummary = () => useQuery({ queryKey: ["summary"], queryFn: () => get<Summary>("/api/v1/summary/weekly") });
export const useRecentWorkouts = () =>
  useQuery({ queryKey: ["workouts"], queryFn: () => get<{ workouts: Workout[] }>("/api/v1/workout/recent?limit=10") });
export const useTargets = () =>
  useQuery({ queryKey: ["targets"], queryFn: () => get<{ current: import("./types").Target | null; history: import("./types").Target[] }>("/api/v1/targets") });
export const useReview = () => useQuery({ queryKey: ["review"], queryFn: () => get<ReviewPayload>("/api/v1/review") });
export const useHealth = () => useQuery({ queryKey: ["health"], queryFn: () => get<Health>("/api/v1/health"), staleTime: 60_000 });

/** Re-fetch everything once queued writes have landed. */
export function useInvalidateOnFlush(): void {
  const qc = useQueryClient();
  useEffect(() => onFlushed(() => void qc.invalidateQueries()), [qc]);
}
