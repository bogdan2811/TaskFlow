CREATE TABLE users (
    user_id BIGSERIAL PRIMARY KEY,
    username VARCHAR(20) NOT NULL UNIQUE,
    email VARCHAR(254) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE chats (
    chat_id BIGSERIAL PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    created_by BIGINT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_chats_created_by
        FOREIGN KEY (created_by)
        REFERENCES users(user_id)
        ON DELETE RESTRICT
);

CREATE TABLE chatparticipants (
    chat_participant_id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_chatparticipants_chat
        FOREIGN KEY (chat_id)
        REFERENCES chats(chat_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_chatparticipants_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE,
    CONSTRAINT uq_chatparticipants UNIQUE (chat_id, user_id)
);

CREATE TABLE messages (
    message_id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    sender_id BIGINT NOT NULL,
    content TEXT NOT NULL,
    sent_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_messages_chat
        FOREIGN KEY (chat_id)
        REFERENCES chats(chat_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_messages_sender
        FOREIGN KEY (sender_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT
);

CREATE TABLE tasks (
    task_id BIGSERIAL PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    description TEXT,
    status VARCHAR(30) NOT NULL DEFAULT 'todo',
    priority VARCHAR(30) NOT NULL DEFAULT 'medium',
    category VARCHAR(100),
    creator_id BIGINT NOT NULL,
    chat_id BIGINT NOT NULL,
    source_message_id BIGINT,
    due_date TIMESTAMP,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_tasks_creator
        FOREIGN KEY (creator_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_tasks_chat
        FOREIGN KEY (chat_id)
        REFERENCES chats(chat_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_tasks_source_message
        FOREIGN KEY (source_message_id)
        REFERENCES messages(message_id)
        ON DELETE SET NULL,
    CONSTRAINT chk_tasks_status
        CHECK (status IN ('todo', 'in_progress', 'blocked', 'done')),
    CONSTRAINT chk_tasks_priority
        CHECK (priority IN ('low', 'medium', 'high', 'critical'))
);

CREATE TABLE taskassignees (
    task_assignee_id BIGSERIAL PRIMARY KEY,
    task_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    assigned_by BIGINT NOT NULL,
    assigned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_taskassignees_task
        FOREIGN KEY (task_id)
        REFERENCES tasks(task_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_taskassignees_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_taskassignees_assigned_by
        FOREIGN KEY (assigned_by)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,
    CONSTRAINT uq_taskassignees UNIQUE (task_id, user_id)
);

-- INDEXURI

CREATE INDEX idx_chats_created_by ON chats(created_by);

CREATE INDEX idx_chatparticipants_chat_id ON chatparticipants(chat_id);
CREATE INDEX idx_chatparticipants_user_id ON chatparticipants(user_id);

CREATE INDEX idx_messages_chat_id ON messages(chat_id);
CREATE INDEX idx_messages_sender_id ON messages(sender_id);
CREATE INDEX idx_messages_sent_at ON messages(sent_at);

CREATE INDEX idx_tasks_creator_id ON tasks(creator_id);
CREATE INDEX idx_tasks_chat_id ON tasks(chat_id);
CREATE INDEX idx_tasks_source_message_id ON tasks(source_message_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_due_date ON tasks(due_date);

CREATE INDEX idx_taskassignees_task_id ON taskassignees(task_id);
CREATE INDEX idx_taskassignees_user_id ON taskassignees(user_id);
CREATE INDEX idx_taskassignees_assigned_by ON taskassignees(assigned_by);
