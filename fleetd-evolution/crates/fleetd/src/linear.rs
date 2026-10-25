use reqwest::header::{HeaderMap, HeaderValue, AUTHORIZATION, CONTENT_TYPE, USER_AGENT};
use serde_json::json;
use std::time::Duration;

#[derive(Clone)]
pub struct LinearClient {
    client: reqwest::Client,
    api_url: String,
    api_key: String,
}

impl LinearClient {
    pub fn new(api_url: &str, api_key: &str) -> Self {
        let mut headers = HeaderMap::new();
        headers.insert(
            AUTHORIZATION,
            HeaderValue::from_str(api_key).unwrap_or_else(|_| HeaderValue::from_static("")),
        );
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
        headers.insert(
            USER_AGENT,
            HeaderValue::from_static("mindrock-fleetd/0.1.0"),
        );

        let client = reqwest::Client::builder()
            .default_headers(headers)
            .timeout(Duration::from_secs(30))
            .build()
            .unwrap_or_default();

        Self {
            client,
            api_url: api_url.to_string(),
            api_key: api_key.to_string(),
        }
    }

    async fn graphql(&self, query: &str, variables: serde_json::Value) -> Result<serde_json::Value, String> {
        if self.api_key.is_empty() {
            return Err("Linear API key is empty".to_string());
        }

        let body = json!({
            "query": query,
            "variables": variables
        });

        let resp = self
            .client
            .post(&self.api_url)
            .json(&body)
            .send()
            .await
            .map_err(|e| format!("HTTP request failed: {}", e))?;

        let status = resp.status();
        let payload: serde_json::Value = resp
            .json()
            .await
            .map_err(|e| format!("Failed to parse JSON (HTTP {}): {}", status, e))?;

        if let Some(errors) = payload.get("errors").and_then(|e| e.as_array()) {
            if !errors.is_empty() {
                let msgs: Vec<String> = errors
                    .iter()
                    .map(|e| e.get("message").and_then(|m| m.as_str()).unwrap_or("unknown").to_string())
                    .collect();
                return Err(format!("GraphQL error: {}", msgs.join("; ")));
            }
        }

        Ok(payload.get("data").cloned().unwrap_or(serde_json::Value::Null))
    }

    pub async fn comment_exists(&self, issue_id: &str, marker: &str) -> bool {
        let q = r#"
            query CheckComment($issueId: String!) {
              issue(id: $issueId) {
                comments(first: 50) {
                  nodes { body }
                }
              }
            }
        "#;

        if let Ok(data) = self.graphql(q, json!({ "issueId": issue_id })).await {
            if let Some(nodes) = data
                .pointer("/issue/comments/nodes")
                .and_then(|n| n.as_array())
            {
                return nodes.iter().any(|c| {
                    c.get("body")
                        .and_then(|b| b.as_str())
                        .map(|b| b.contains(marker))
                        .unwrap_or(false)
                });
            }
        }
        false
    }

    pub async fn post_comment(&self, issue_id: &str, body: &str) -> Result<bool, String> {
        let q = r#"
            mutation AddComment($input: CommentCreateInput!) {
              commentCreate(input: $input) {
                success
                comment { id }
              }
            }
        "#;

        let res = self
            .graphql(q, json!({ "input": { "issueId": issue_id, "body": body } }))
            .await?;

        Ok(res
            .pointer("/commentCreate/success")
            .and_then(|s| s.as_bool())
            .unwrap_or(false))
    }

    pub async fn set_in_review_and_qa_label(&self, issue_id: &str) -> Result<(), String> {
        // 1. Resolve State
        let state_query = r#"
            query GetStates($issueId: String!) {
              issue(id: $issueId) {
                team {
                  states {
                    nodes { id name }
                  }
                }
              }
            }
        "#;

        let data = self.graphql(state_query, json!({ "issueId": issue_id })).await?;
        let state_id = data
            .pointer("/issue/team/states/nodes")
            .and_then(|n| n.as_array())
            .and_then(|nodes| {
                nodes.iter().find_map(|s| {
                    let name = s.get("name").and_then(|n| n.as_str())?;
                    if name.eq_ignore_ascii_case("In Review") {
                        s.get("id").and_then(|id| id.as_str())
                    } else {
                        None
                    }
                })
            });

        if let Some(sid) = state_id {
            let update_q = r#"
                mutation MoveState($id: String!, $stateId: String!) {
                  issueUpdate(id: $id, input: { stateId: $stateId }) { success }
                }
            "#;
            let _ = self.graphql(update_q, json!({ "id": issue_id, "stateId": sid })).await;
        }

        // 2. Resolve Label
        let label_query = r#"
            query GetLabel($name: String!) {
              issueLabels(filter: { name: { eq: $name } }, first: 5) {
                nodes { id name }
              }
            }
        "#;

        let ldata = self
            .graphql(label_query, json!({ "name": "Role: QA-Needed" }))
            .await?;

        let label_id = ldata
            .pointer("/issueLabels/nodes")
            .and_then(|n| n.as_array())
            .and_then(|nodes| nodes.first())
            .and_then(|l| l.get("id"))
            .and_then(|id| id.as_str());

        if let Some(lid) = label_id {
            let add_lbl_q = r#"
                mutation AddLabel($issueId: String!, $labelId: String!) {
                  issueAddLabel(id: $issueId, labelId: $labelId) { success }
                }
            "#;
            let _ = self
                .graphql(add_lbl_q, json!({ "issueId": issue_id, "labelId": lid }))
                .await;
        }

        Ok(())
    }

    pub async fn set_assignee(&self, issue_id: &str, user_id: &str) -> Result<(), String> {
        let q = r#"
            mutation AssignUser($issueId: String!, $userId: String!) {
              issueUpdate(id: $issueId, input: { assigneeId: $userId }) {
                success
              }
            }
        "#;
        self.graphql(q, json!({ "issueId": issue_id, "userId": user_id })).await?;
        Ok(())
    }

    pub async fn set_in_progress(&self, issue_id: &str) -> Result<(), String> {
        let state_query = r#"
            query GetStates($issueId: String!) {
              issue(id: $issueId) {
                team {
                  states {
                    nodes { id name }
                  }
                }
              }
            }
        "#;

        let data = self.graphql(state_query, json!({ "issueId": issue_id })).await?;
        let state_id = data
            .pointer("/issue/team/states/nodes")
            .and_then(|n| n.as_array())
            .and_then(|nodes| {
                nodes.iter().find_map(|s| {
                    let name = s.get("name").and_then(|n| n.as_str())?;
                    if name.eq_ignore_ascii_case("In Progress") {
                        s.get("id").and_then(|id| id.as_str())
                    } else {
                        None
                    }
                })
            });

        if let Some(sid) = state_id {
            let update_q = r#"
                mutation MoveState($id: String!, $stateId: String!) {
                  issueUpdate(id: $id, input: { stateId: $stateId }) { success }
                }
            "#;
            let _ = self.graphql(update_q, json!({ "id": issue_id, "stateId": sid })).await;
        }

        Ok(())
    }

    pub async fn fetch_vacuum_candidates(
        &self,
        team_keys: &[String],
        limit: usize,
    ) -> Result<Vec<LinearIssueCandidate>, String> {
        let q = r#"
            query VacuumCandidates($teamKeys: [String!], $limit: Int!) {
              issues(
                filter: {
                  team: { key: { in: $teamKeys } }
                  state: { type: { in: ["backlog", "unstarted"] } }
                  assignee: { null: true }
                }
                first: $limit
                orderBy: updatedAt
              ) {
                nodes {
                  id
                  identifier
                  title
                  description
                  labels {
                    nodes {
                      name
                    }
                  }
                }
              }
            }
        "#;

        let data = self
            .graphql(
                q,
                json!({
                    "teamKeys": team_keys,
                    "limit": limit
                }),
            )
            .await?;

        let nodes = match data.pointer("/issues/nodes").and_then(|n| n.as_array()) {
            Some(arr) => arr,
            None => return Ok(Vec::new()),
        };

        let mut candidates = Vec::new();
        for node in nodes {
            let id = node.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let identifier = node.get("identifier").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let title = node.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let description = node.get("description").and_then(|v| v.as_str()).unwrap_or("").to_string();

            let mut labels = Vec::new();
            if let Some(lbl_nodes) = node.pointer("/labels/nodes").and_then(|l| l.as_array()) {
                for l in lbl_nodes {
                    if let Some(name) = l.get("name").and_then(|n| n.as_str()) {
                        labels.push(name.to_string());
                    }
                }
            }

            candidates.push(LinearIssueCandidate {
                id,
                identifier,
                title,
                description,
                labels,
            });
        }

        Ok(candidates)
    }
}

#[derive(Clone, Debug)]
pub struct LinearIssueCandidate {
    pub id: String,
    pub identifier: String,
    pub title: String,
    pub description: String,
    pub labels: Vec<String>,
}
