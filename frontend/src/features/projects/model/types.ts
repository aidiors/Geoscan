import type { SavedScenario } from "@/features/planner/storage/scenario";
import type { PlanRequest, PlanResponse, SurveyQualityReport, TurnRadiusAnalysisResult, ZoneType } from "@/shared/api/types";

export interface SourceFile { name: string; zone_type: ZoneType; content: string; sha256?: string | null }
export interface ProjectDocument {
  schema_version: 1;
  editor_state: SavedScenario;
  source_files: SourceFile[];
  request: PlanRequest | null;
  plan: PlanResponse | null;
  survey_quality: SurveyQualityReport | null;
  turn_radius_analysis: TurnRadiusAnalysisResult | null;
}
export interface ProjectSummary { project_id: string; name: string; latest_revision: number; updated_at: string }
export interface ProjectRevision { project_id: string; name: string; revision: number; created_at: string; document_sha256: string; document: ProjectDocument }
