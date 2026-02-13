import React, { useState, useEffect, useCallback, useContext } from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faComments, faUpload } from '@fortawesome/free-solid-svg-icons';
import { ConfigContext } from './components/ConfigContext';
import styled from 'styled-components';
import Header from './components/Header';
import ChatSection from './components/ChatSection';
import UploadSection from './components/UploadSection';
import IndexRibbon from './components/IndexRibbon';

const Container = styled.div`
  font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
  max-width: 1200px;
  margin: 0 auto;
  padding: 20px;
  background: ${props => props.theme.backgroundColor};
  color: ${props => props.theme.textColor};
  border-radius: 8px;
  box-shadow: 0 0 15px rgba(0, 0, 0, 0.1);
  height: 90vh;
  display: flex;
  flex-direction: column;
  transition: all 0.3s ease;
`;

const MainContent = styled.div`
  display: flex;
  flex: 1;
  overflow: hidden;
`;

const Sidebar = styled.div`
  width: 200px;
  overflow-y: auto;
  margin-top: 25px;
`;

const ContentArea = styled.div`
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  padding-left: 20px;
`;

const LoadingIndicator = styled.div`
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100%;
  font-size: 18px;
  color: #666;
`;

// Navigation Styles for Sidebar
const NavContainer = styled.div`
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 15px;
  margin-top: 25px;
`;

const NavButton = styled.button`
  background: ${(props) => (props.active ? "#B7410E" : "transparent")};
  border: 1px solid ${(props) => (props.active ? "#B7410E" : props.theme.borderColor)};
  color: ${(props) => (props.active ? "#ffffff" : props.theme.textColor)};
  padding: 12px;
  border-radius: 8px;
  cursor: pointer;
  text-align: left;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 10px;
  transition: all 0.2s;

  &:hover {
    background: ${(props) => (props.active ? "#8B3A0E" : props.theme.buttonHoverBackground)};
    transform: translateX(3px);
  }
`;


const Divider = styled.div`
  height: 1px;
  background-color: ${props => props.theme.borderColor};
  margin: 10px 20px;
`;

function App({ toggleTheme, isDarkMode }) {
  const { operationsRestricted } = useContext(ConfigContext);
  const [activeSection, setActiveSection] = useState('chat');
  const [indexes, setIndexes] = useState([]);
  const [selectedIndex, setSelectedIndex] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [hasLoadedBefore, setHasLoadedBefore] = useState(false);

  useEffect(() => {
    fetchIndexes();
  }, []);

  const fetchIndexes = useCallback(async () => {
    try {
      const response = await fetch('/indexes');
      const data = await response.json();
      // Backwards compatibility for data.indexes being array of arrays
      const normalizedIndexes = (data.indexes || []).map(idx =>
        Array.isArray(idx) ? { name: idx[0], is_restricted: idx[1] } : idx
      );
      setIndexes(normalizedIndexes);
      setHasLoadedBefore(true);
    } catch (error) {
      console.error('Error loading indexes:', error);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const handleSelectIndex = useCallback((index) => {
    const normalized = Array.isArray(index) ? { name: index[0], is_restricted: index[1] } : index;
    setSelectedIndex(normalized);
    localStorage.setItem('lastUsedIndex', JSON.stringify(normalized));
  }, []);



  const handleDeleteIndex = useCallback((indexName, isRestricted) => {
    setIndexes(prevIndexes => prevIndexes.filter(index => {
      const name = Array.isArray(index) ? index[0] : index.name;
      const restricted = Array.isArray(index) ? index[1] : index.is_restricted;
      return !(name === indexName && restricted === isRestricted);
    }));

    if (selectedIndex && selectedIndex.name === indexName && selectedIndex.is_restricted === isRestricted) {
      setSelectedIndex(null);
      localStorage.removeItem('lastUsedIndex');
    }
  }, [selectedIndex]);

  useEffect(() => {
    fetchIndexes();
  }, [fetchIndexes]);

  useEffect(() => {
    if (indexes.length > 0 && !selectedIndex) {
      const lastUsedIndex = localStorage.getItem('lastUsedIndex');
      if (lastUsedIndex) {
        try {
          const parsedIndex = JSON.parse(lastUsedIndex);
          let name, is_restricted;
          if (Array.isArray(parsedIndex)) {
            [name, is_restricted] = parsedIndex;
          } else {
            name = parsedIndex.name;
            is_restricted = parsedIndex.is_restricted;
          }

          const foundIndex = indexes.find(index => {
            const iName = Array.isArray(index) ? index[0] : index.name;
            const iRestricted = Array.isArray(index) ? index[1] : index.is_restricted;
            return iName === name && iRestricted === is_restricted;
          });

          if (foundIndex) {
            setSelectedIndex(Array.isArray(foundIndex) ? { name: foundIndex[0], is_restricted: foundIndex[1] } : foundIndex);
          } else {
            const first = indexes[0];
            setSelectedIndex(Array.isArray(first) ? { name: first[0], is_restricted: first[1] } : first);
          }
        } catch (e) {
          const first = indexes[0];
          setSelectedIndex(Array.isArray(first) ? { name: first[0], is_restricted: first[1] } : first);
        }
      } else {
        const first = indexes[0];
        setSelectedIndex(Array.isArray(first) ? { name: first[0], is_restricted: first[1] } : first);
      }
    }
  }, [indexes, selectedIndex]);

  return (

    <Container>
      <Header
        activeSection={activeSection}
        setActiveSection={setActiveSection}
        toggleTheme={toggleTheme}
        isDarkMode={isDarkMode}
      />
      <MainContent>
        <Sidebar>
          {isLoading && !hasLoadedBefore ? (
            <LoadingIndicator>Loading indexes...</LoadingIndicator>
          ) : (
            <>
              <IndexRibbon
                indexes={indexes}
                selectedIndex={selectedIndex}
                onSelectIndex={handleSelectIndex}
                onIndexesChange={fetchIndexes}
                onDeleteIndex={handleDeleteIndex}
              />

              <Divider />

              <NavContainer>
                <NavButton
                  active={activeSection === 'upload'}
                  onClick={() => setActiveSection('upload')}
                >
                  <FontAwesomeIcon icon={faUpload} /> Upload
                </NavButton>

                <NavButton
                  active={activeSection === 'chat'}
                  onClick={() => setActiveSection('chat')}
                >
                  <FontAwesomeIcon icon={faComments} /> Chat
                </NavButton>


              </NavContainer>
            </>
          )}
        </Sidebar>

        <ContentArea>
          {selectedIndex ? (
            <>
              {activeSection === 'chat' && (
                <ChatSection
                  indexName={selectedIndex.name}
                  isRestricted={selectedIndex.is_restricted}
                />
              )}
              {activeSection === 'upload' && (
                <UploadSection
                  indexName={selectedIndex.name}
                  isRestricted={selectedIndex.is_restricted}
                  onFilesChange={fetchIndexes}
                />
              )}

            </>
          ) : (
            <LoadingIndicator>Select an index to begin</LoadingIndicator>
          )}
        </ContentArea>
      </MainContent>
    </Container>

  );
}

export default App;