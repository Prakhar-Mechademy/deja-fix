DATABASE:


users: id, email, created_at
sessions: id, user_id, created_at, status
messages: id, session_id, role, content, timestamp
solutions: id, originating_session_id, problem, 
            symptoms, environment, fix_steps, 
            failed_attempts, embedding, verified, 
            confirmation_count
llm_usage: id, session_id, model, input_tokens,    
            output_tokens, purpose, timestamp


every session belongs to exactly one user.
every message belongs to exactly one session.
every solution references its originating session.
only verified solutions can be retreived.
embeddings are stored alongside solutions. 
every gemini call generates a token-usage record. 

customer memory: previous conversations belong to an indv. customer
shared knowledge: sanitised, verified solutions accessible across customers. 


a session needs to have  messages, solution from gemini, and an llm usage report. 



API design:

