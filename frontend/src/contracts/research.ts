export interface ResearchRequest {
  intent: string;
  workflow: 'reproduce' | 'benchmark';
  code?: string;
  attachments: string[];
  research: {
    repository?: { url: string; ref: string };
    environment: { requirements: string[] };
    entrypoint: string;
    max_repairs: number;
    benchmark?: {
      upload_id: string;
      format: 'csv' | 'tsv' | 'jsonl';
      input_columns: string[];
      target_column: string;
      id_column?: string;
      task: 'classification' | 'regression';
      metrics: string[];
    };
    ablations: { name: string; arguments: string[] }[];
    rubric: {
      claims: {
        title: string;
        statement: string;
        criteria: { description: string; metric_name: string; expected_value: number;
          operator: 'at_least' | 'at_most'; tolerance: number }[];
      }[];
    };
  };
}
