import os
import unittest
from unittest.mock import Mock, patch, AsyncMock
import pandas as pd
from app.query.graphrag_query import GraphRagQuery
from app.integration.graphrag_config import GraphRagConfig

class TestGraphRagQuery(unittest.TestCase):
   def setUp(self):
       self.mock_config = Mock(spec=GraphRagConfig)
       self.mock_config.prefix = "test-prefix"
       self.mock_config.index_name = "test-index"
       self.mock_config.get_config.return_value = {"storage": {}, "llm": {}}
       self.query = GraphRagQuery(self.mock_config)
       # Force local mode in tests
       self.query.env = "local"
   @patch.object(GraphRagQuery, "_read_parquet_local")
   def test_get_reports_local(self, mock_read_local):
       # Return two dataframes in the order they are read in _get_reports
       entity_df = pd.DataFrame({"entity": [1, 2, 3]})
       report_df = pd.DataFrame({"community": [1, 2], "title": ["A", "B"], "content": ["c1", "c2"]})
       mock_read_local.side_effect = [entity_df, report_df]
       # Use local style paths
       entity_table_path = os.path.join("output", "test-index", "create_final_nodes.parquet")
       community_report_table_path = os.path.join("output", "test-index", "create_final_community_reports.parquet")
       rep_df, ent_df = self.query._get_reports(entity_table_path, community_report_table_path, 1)
       self.assertIsInstance(rep_df, pd.DataFrame)
       self.assertIsInstance(ent_df, pd.DataFrame)
       self.assertEqual(len(rep_df), 2)
       self.assertEqual(len(ent_df), 3)

class TestGraphRagQueryAsync(unittest.IsolatedAsyncioTestCase):
   async def asyncSetUp(self):
       self.mock_config = Mock(spec=GraphRagConfig)
       self.mock_config.prefix = "test-prefix"
       self.mock_config.index_name = "test-index"
       self.mock_config.get_config.return_value = {"storage": {}, "llm": {}}
       self.query = GraphRagQuery(self.mock_config)
       self.query.env = "local"
   @patch.object(GraphRagQuery, "_get_reports")
   @patch("app.query.graphrag_query.ChatOpenAI")
   @patch("app.query.graphrag_query.tiktoken.encoding_for_model")
   @patch("app.query.graphrag_query.GlobalSearch")
   @patch("app.query.graphrag_query.GlobalCommunityContext")
   @patch("app.query.graphrag_query.read_indexer_reports")
   async def test_global_query(
       self,
       mock_read_indexer_reports,
       mock_global_community_context,
       mock_global_search,
       mock_tiktoken,
       mock_chat_openai,
       mock_get_reports,
   ):
       # Fake dataframes returned by _get_reports
       report_df = pd.DataFrame(
           {"community": [1, 2], "title": ["Report 1", "Report 2"], "content": ["Content 1", "Content 2"]}
       )
       entity_df = pd.DataFrame({"entity": [1, 2, 3]})
       mock_get_reports.return_value = (report_df, entity_df)
       # Fake search result object
       mock_search_result = Mock()
       mock_search_result.response = "Test response"
       mock_search_result.context_data = {
           "reports": pd.DataFrame(
               {
                   "title": ["test-index<sep>1<sep>Report 1", "test-index<sep>2<sep>Report 2"],
                   "content": ["Content 1", "Content 2"],
                   "rank": [0.9, 0.8],
               }
           )
       }
       mock_global_search.return_value.asearch = AsyncMock(return_value=mock_search_result)
       result, context_data = await self.query.global_query("test query")
       self.assertEqual(result, "Test response")
       self.assertIsInstance(context_data, dict)
       self.assertIn("reports", context_data)
       self.assertIsInstance(context_data["reports"], list)
       self.assertEqual(len(context_data["reports"]), 2)
       self.assertEqual(context_data["reports"][0]["index_name"], "test-index")
       self.assertEqual(context_data["reports"][0]["index_id"], "1")
       self.assertEqual(context_data["reports"][0]["title"], "Report 1")

if __name__ == "__main__":
   unittest.main()